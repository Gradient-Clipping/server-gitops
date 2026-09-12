#!/usr/bin/env python3
"""Apply Agent bootstrap from the exact CI-validated current production tree."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess

from production_gate import api, validate_run

ROOT = Path(__file__).resolve().parents[1]
SERVER = "root@1.14.95.189"
RUNTIME_DIGEST = "81416511897ab8abd4e723d66823c5b0461a2ee3311cfa70d152404ef9b860cf"
RUNTIME_CACHE = "/var/cache/platform-agent/gvisor-release-20260907.0.tar.bz2"


def run(args, data=None, binary=False, timeout=180):
    result = subprocess.run(args, input=data, capture_output=True, text=not binary,
                            **({} if binary else {"encoding": "utf-8"}), timeout=timeout, cwd=ROOT)
    if result.returncode:
        # Never echo command payloads or captured stderr: provisioning contains secrets.
        raise RuntimeError(f"{Path(args[0]).name} failed with exit {result.returncode}; output withheld")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--nginx-only", action="store_true")
    parser.add_argument("--runtime-archive", type=Path)
    parser.add_argument("--restart-k3s", action="store_true")
    args = parser.parse_args()
    if args.nginx_only:
        if args.env_file or args.runtime_archive or args.restart_k3s:
            parser.error("--nginx-only cannot change runtime or credentials")
    elif not args.env_file:
        parser.error("--env-file is required for full bootstrap")
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise ValueError("A full GitOps revision is required")
    config = json.loads((ROOT / "config/production.json").read_text())
    prefix = "repos/" + config["repository"]
    validation = api(f"{prefix}/actions/runs/{args.run_id}")
    jobs = api(f"{prefix}/actions/runs/{args.run_id}/jobs?filter=latest&per_page=100")["jobs"]
    validate_run(config, args.revision, validation, jobs)
    if validation.get("conclusion") != "success" or api(f"{prefix}/git/ref/heads/production")["object"]["sha"] != args.revision:
        raise ValueError("Bootstrap requires the current successfully validated production revision")
    if args.runtime_archive:
        args.runtime_archive = args.runtime_archive.resolve(strict=True)
        if not args.runtime_archive.is_file():
            raise ValueError("Runtime archive must be an existing regular file")
        digest = hashlib.sha256()
        with args.runtime_archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != RUNTIME_DIGEST:
            raise ValueError("Local gVisor runtime archive checksum mismatch")
    run(["git", "fetch", "origin", "production"])
    for name in ("scripts/run_agent_bootstrap.py", "scripts/production_gate.py", "config/production.json"):
        expected = run(["git", "show", args.revision + ":" + name], binary=True)
        if (ROOT / name).read_bytes().replace(b"\r\n", b"\n") != expected.replace(b"\r\n", b"\n"):
            raise ValueError("Local runner or gate differs from the validated production commit")
    archive = run(["git", "archive", "--format=tar", args.revision,
                   "scripts/bootstrap_agent.py", "host/agent", "host/k3s/config.yaml", "config/agent-views.sql"], binary=True)
    remote = "/var/lib/platform-gitops/" + args.revision
    ssh = [shutil.which("ssh.exe") or "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", SERVER]
    if args.runtime_archive:
        run(ssh + ["install -d -m 0700 /var/cache/platform-agent"])
        staged_cache = RUNTIME_CACHE + "." + args.revision + ".partial"
        run([shutil.which("scp.exe") or "scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
             str(args.runtime_archive), SERVER + ":" + staged_cache], timeout=900)
        # Host bootstrap verifies the pinned digest again before installing binaries.
        run(ssh + ["chmod 0600 " + shlex.quote(staged_cache) + " && mv -f "
                   + shlex.quote(staged_cache) + " " + shlex.quote(RUNTIME_CACHE)])
    run(ssh + ["install -d -m 0700 " + shlex.quote(remote) + " && tar -xf - -C " + shlex.quote(remote)], archive, binary=True)
    if args.nginx_only:
        if api(f"{prefix}/git/ref/heads/production")["object"]["sha"] != args.revision:
            raise ValueError("Production changed before ingress application")
        result = run(ssh + [shlex.join(["python3", remote + "/scripts/bootstrap_agent.py", "--nginx-only"])])
        print(result.strip())
        return
    env_target = "/etc/platform-secrets/lazycampus-agent.env"
    run(ssh + ["umask 077; cat > " + shlex.quote(env_target)], args.env_file.read_bytes(), binary=True)
    command = ["python3", remote + "/scripts/bootstrap_agent.py", "--env-file", env_target]
    if args.runtime_archive:
        command.extend(["--runtime-archive", RUNTIME_CACHE])
    if args.restart_k3s:
        command.append("--restart-k3s")
    result = run(ssh + [shlex.join(command)], timeout=1200)
    print(result.strip())


if __name__ == "__main__":
    main()
