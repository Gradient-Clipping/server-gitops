"""Apply reviewed Agent host configuration and private runtime credentials.

Run only from the CI-validated GitOps revision via run_agent_bootstrap.py.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
NS = "lazycampus-agent"
RELEASE = "release-20260907.0"
DIGEST = "81416511897ab8abd4e723d66823c5b0461a2ee3311cfa70d152404ef9b860cf"
STATE = Path("/var/lib/platform-agent-bootstrap")


def command(args, content=None, timeout=120):
    result = subprocess.run(args, input=content, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed (exit {result.returncode}); command output withheld")
    return result.stdout


def kubectl(*args, content=None):
    return command(["k3s", "kubectl", *args], content)


def apply_secret(name, values, secret_type="Opaque", namespace=NS):
    resource = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": namespace},
                "type": secret_type, "data": {key: base64.b64encode(value.encode()).decode() for key, value in values.items()}}
    kubectl("apply", "--server-side", "--field-manager=agent-bootstrap", "-f", "-", content=json.dumps(resource))


def read_env(path):
    values = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("Invalid runtime env format")
        parsed = shlex.split(value, comments=False)
        values[key] = parsed[0] if len(parsed) == 1 else value
    return values


def copy_managed(source, destination, mode=0o644):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = source.read_bytes()
    if destination.exists() and destination.read_bytes() == payload:
        return False
    backup = STATE / "before" / destination.relative_to("/")
    if destination.exists() and not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destination, backup)
    destination.write_bytes(payload)
    destination.chmod(mode)
    return True


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_runtime(url, destination):
    """Bound each attempt so a slow download cannot stall bootstrap indefinitely."""
    for attempt in range(3):
        try:
            deadline = time.monotonic() + 180
            with urllib.request.urlopen(url, timeout=30) as response, destination.open("wb") as output:
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("gVisor download exceeded its attempt time budget")
                    chunk = response.read1(128 * 1024)
                    if not chunk:
                        return
                    output.write(chunk)
        except (OSError, TimeoutError):
            destination.unlink(missing_ok=True)
            if attempt == 2:
                raise RuntimeError("gVisor download failed after three bounded attempts; use --runtime-archive") from None
            time.sleep(attempt + 1)


def install_runtime(runtime_archive=None):
    marker = STATE / "gvisor-version"
    if marker.exists() and marker.read_text().strip() == RELEASE:
        return False
    url = f"https://github.com/google/gvisor/releases/download/{RELEASE}/gvisor-x86_64.tar.bz2"
    with tempfile.TemporaryDirectory(prefix="agent-gvisor-") as temp:
        if runtime_archive is None:
            archive = Path(temp) / "runtime.tar.bz2"
            download_runtime(url, archive)
        else:
            archive = runtime_archive.resolve(strict=True)
            if not archive.is_file():
                raise ValueError("Runtime archive must be an existing regular file")
        if file_digest(archive) != DIGEST:
            raise ValueError("gVisor archive checksum mismatch")
        with tarfile.open(archive) as bundle:
            for name in ("runsc", "containerd-shim-runsc-v1"):
                candidates = [member for member in bundle if member.isfile() and Path(member.name).name == name]
                if len(candidates) != 1:
                    raise ValueError("Unexpected gVisor binary archive layout")
                source = bundle.extractfile(candidates[0])
                output = Path("/usr/local/bin") / name
                output.write_bytes(source.read())
                output.chmod(0o755)
    marker.write_text(RELEASE + "\n")
    return True


def provision(values):
    kubectl("apply", "--server-side", "--field-manager=agent-bootstrap", "-f", "-", content=json.dumps(
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS}}))
    sets = {
        "agent-model": ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "API_BASE"),
        "agent-api": ("BRIDGE_TOKEN", "AGENT_TOKEN"),
        "agent-opencode": ("OPENCODE_SERVER_PASSWORD",),
        "agent-qq": ("QQBOT_ID", "QQBOT_SECRET"),
        "agent-admin": ("ASTRBOT_ADMIN_PASSWORD",),
    }
    for name, keys in sets.items():
        apply_secret(name, {key: values[key] for key in keys})
    secret_dir = Path("/etc/platform-secrets")
    username = (secret_dir / "tcr-username").read_text().strip()
    password = (secret_dir / "tcr-password").read_text().strip()
    auth = base64.b64encode((username + ":" + password).encode()).decode()
    registry_config = {".dockerconfigjson": json.dumps({"auths": {
        "ccr.ccs.tencentyun.com": {"username": username, "password": password, "auth": auth}}})}
    apply_secret("tcr-auth", registry_config, "kubernetes.io/dockerconfigjson")
    kubectl("apply", "--server-side", "--field-manager=agent-bootstrap", "-f", "-", content=json.dumps(
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "agent-sandbox-system"}}))
    apply_secret("tcr-auth", registry_config, "kubernetes.io/dockerconfigjson", namespace="agent-sandbox-system")
    source = json.loads(kubectl("get", "secret", "mysql-easy-swu", "-n", "easy-swu", "-o", "json"))["data"]
    database = base64.b64decode(source["MYSQL_DATABASE"]).decode()
    if not re.fullmatch(r"[a-zA-Z0-9_]+", database):
        raise ValueError("Unexpected source database identifier")
    account_file = secret_dir / "agent-snapshot-password"
    if not account_file.exists():
        account_file.write_text(secrets.token_urlsafe(36))
        account_file.chmod(0o600)
    account_password = account_file.read_text().strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{32,}", account_password):
        raise ValueError("Unexpected snapshot password format")
    sql = (
        "CREATE USER IF NOT EXISTS 'agent_view_owner'@'localhost' ACCOUNT LOCK;\n"
        "ALTER USER 'agent_view_owner'@'localhost' ACCOUNT LOCK;\n"
        f"CREATE USER IF NOT EXISTS 'agent_snapshot'@'%' IDENTIFIED BY '{account_password}';\n"
        f"ALTER USER 'agent_snapshot'@'%' IDENTIFIED BY '{account_password}';\n"
        "REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'agent_snapshot'@'%';\n"
        "REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'agent_view_owner'@'localhost';\n"
        + (ROOT / "config/agent-views.sql").read_text().replace("`agent_source_database`", "`" + database + "`")
    )
    kubectl("exec", "-i", "-n", "mysql-system", "mysql-0", "--", "sh", "-c",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --user=root --batch', content=sql)
    apply_secret("agent-snapshot-mysql", {
        "EASY_CAMPUS_DB_HOST": "mysql.mysql-system.svc.cluster.local",
        "EASY_CAMPUS_DB_PORT": "3306", "EASY_CAMPUS_DB_NAME": database,
        "EASY_CAMPUS_DB_USER": "agent_snapshot", "EASY_CAMPUS_DB_PASSWORD": account_password,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--runtime-archive", type=Path)
    parser.add_argument("--restart-k3s", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0 or os.uname().machine != "x86_64":
        raise ValueError("This deployment targets the documented x86_64 K3s node as root")
    values = read_env(args.env_file)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    template = Path("/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl")
    expected = ROOT / "host/agent/config-v3.toml.tmpl"
    if template.exists() and template.read_bytes() != expected.read_bytes() and "agent-runsc" not in template.read_text():
        raise ValueError("An existing custom containerd template requires a reviewed merge")
    changed = install_runtime(args.runtime_archive)
    changed |= copy_managed(expected, template)
    changed |= copy_managed(ROOT / "host/agent/agent-runsc.toml", "/etc/containerd/agent-runsc.toml")
    changed |= copy_managed(ROOT / "host/agent/audit-policy.yaml", "/etc/rancher/k3s/audit-policy.yaml")
    changed |= copy_managed(ROOT / "host/k3s/config.yaml", "/etc/rancher/k3s/config.yaml", 0o600)
    restart_marker = STATE / "restart-required"
    if changed:
        restart_marker.touch()
    Path("/var/log/kubernetes").mkdir(mode=0o700, exist_ok=True)
    backup_directory = Path("/srv/k3s-backups/lazycampus-agent")
    backup_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(backup_directory, 1000, 1000)
    backup_directory.chmod(0o700)
    for name in ("workspace", "state", "published", "astrbot", "workspace/data", "workspace/tmp"):
        directory = Path("/srv/k3s-data/lazycampus-agent") / name
        directory.mkdir(parents=True, exist_ok=True, mode=0o2770)
        os.chown(directory, 1000, 1000)
        directory.chmod(0o2770)
    copy_managed(ROOT / "host/agent/agent-nginx", "/etc/nginx/sites-available/lazycampus-agent")
    enabled = Path("/etc/nginx/sites-enabled/lazycampus-agent")
    if not enabled.exists():
        enabled.symlink_to("/etc/nginx/sites-available/lazycampus-agent")
    command(["nginx", "-t"])
    command(["systemctl", "reload", "nginx"])
    provision(values)
    if restart_marker.exists() and not args.restart_k3s:
        raise ValueError("Runtime files installed; repeat with --restart-k3s to activate the reviewed runtime")
    if restart_marker.exists():
        command(["systemctl", "restart", "k3s"], timeout=180)
        command(["k3s", "kubectl", "wait", "node/easy-platform-1", "--for=condition=Ready", "--timeout=120s"], timeout=150)
        restart_marker.unlink()
    (STATE / "last-success.json").write_text(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "runtime": RELEASE}))
    print("Agent runtime, audit policy, storage, ingress and scoped credentials applied; no credentials displayed.")


if __name__ == "__main__":
    main()
