#!/usr/bin/env python3
"""Run and restore-check one Agent backup from the exact validated production tree."""
import argparse
import ast
import copy
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid

from production_gate import api, validate_run

ROOT = Path(__file__).resolve().parents[1]
SERVER = "root@1.14.95.189"
NAMESPACE = "lazycampus-agent"
BACKUPS = "/srv/k3s-backups/lazycampus-agent"
APP = "clusters/easy-platform/apps/lazycampus-agent/"
SELF = "scripts/run_agent_backup.py"
FILES = ("config/production.json", "scripts/production_gate.py", SELF, APP + "backup.yaml", APP + "backup_agent.py")


def command(args, data=None, timeout=90):
    try:
        result = subprocess.run([str(x) for x in args], input=data, capture_output=True,
                                cwd=ROOT, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Managed backup command exceeded its time budget") from None
    if result.returncode:
        raise RuntimeError(f"Managed backup command failed with exit {result.returncode}; output withheld")
    return result.stdout


def ssh(args, data=None, timeout=90):
    executable = shutil.which("ssh.exe") or shutil.which("ssh") or "ssh"
    return command([executable, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", SERVER, *args], data, timeout)


def kube(*args, data=None):
    # All arguments are fixed literals or names generated/strictly checked here.
    return ssh(["k3s", "kubectl", "-n", NAMESPACE, *args], data=data)


def archived(revision):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("A complete production commit SHA is required")
    command(["git", "fetch", "origin", "production"])
    payload = command(["git", "archive", "--format=tar", revision, *FILES])
    contents = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for name in FILES:
            member = archive.getmember(name)
            if not member.isfile() or member.size > 1024 * 1024:
                raise ValueError("Unexpected file in selected Git archive")
            contents[name] = archive.extractfile(member).read()
    for name in (SELF, "scripts/production_gate.py"):
        if (ROOT / name).read_bytes().replace(b"\r\n", b"\n") != contents[name].replace(b"\r\n", b"\n"):
            raise ValueError("Runner or gate helper differs from the committed revision")
    return contents


def gate(config, revision, run_id):
    expected = {"repository": "Gradient-Clipping/server-gitops", "sourceBranch": "main", "productionBranch": "production", "validationWorkflow": ".github/workflows/validate.yml", "validationJob": "validate"}
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError("Unexpected production gate configuration")
    prefix = "repos/" + config["repository"]
    run = api(f"{prefix}/actions/runs/{run_id}")
    jobs = api(f"{prefix}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")["jobs"]
    validate_run(config, revision, run, jobs)
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ValueError("Validation run must have completed successfully")
    if api(f"{prefix}/git/ref/heads/production")["object"]["sha"] != revision:
        raise ValueError("Backup requires the exact current production revision")


def make_job(cron, configmap_name, revision, run_id):
    if cron.get("kind") != "CronJob" or cron.get("apiVersion") != "batch/v1" or cron.get("metadata", {}).get("name") != "agent-backup" or cron.get("metadata", {}).get("namespace") != NAMESPACE:
        raise ValueError("Expected only the Agent backup CronJob")
    if not re.fullmatch(r"agent-backup-script(?:-[a-z0-9]{10})?", configmap_name):
        raise ValueError("Unexpected managed backup ConfigMap")
    spec = copy.deepcopy(cron["spec"]["jobTemplate"]["spec"])
    if set(spec) != {"backoffLimit", "activeDeadlineSeconds", "ttlSecondsAfterFinished", "template"} or spec["backoffLimit"] != 1 or spec["activeDeadlineSeconds"] != 1200 or spec["ttlSecondsAfterFinished"] != 172800:
        raise ValueError("Unexpected backup execution/retention limits")
    pod = spec["template"]["spec"]
    if set(pod) != {"automountServiceAccountToken", "restartPolicy", "nodeSelector", "imagePullSecrets", "securityContext", "containers", "volumes"}:
        raise ValueError("Backup pod contains unsupported fields")
    if pod["automountServiceAccountToken"] is not False or pod["restartPolicy"] != "Never" or pod["nodeSelector"] != {"kubernetes.io/hostname": "easy-platform-1"} or pod["imagePullSecrets"] != [{"name": "tcr-auth"}]:
        raise ValueError("Unexpected backup pod access or scheduling")
    if pod["securityContext"] != {"runAsUser": 1000, "runAsGroup": 1000, "runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}}:
        raise ValueError("Backup must run as UID/GID 1000 without a service account token")
    expected_volumes = [{"name": name, "persistentVolumeClaim": {"claimName": "agent-" + name}} for name in ("workspace", "state", "published", "astrbot")]
    expected_volumes += [{"name": "backup", "hostPath": {"path": BACKUPS, "type": "Directory"}}, {"name": "script", "configMap": {"name": "agent-backup-script", "defaultMode": 292}}]
    if pod["volumes"] != expected_volumes:
        raise ValueError("Backup must mount only the four Agent PVCs and fixed backup/script volumes")
    containers = pod["containers"]
    if len(containers) != 1:
        raise ValueError("Backup must have exactly one container")
    container = containers[0]
    if set(container) != {"name", "image", "command", "resources", "securityContext", "volumeMounts"} or container["name"] != "backup" or container["command"] != ["python", "/scripts/backup_agent.py"]:
        raise ValueError("Backup must execute only the committed backup script")
    if not re.fullmatch(r"ccr\.ccs\.tencentyun\.com/lazycampus/agent-backend:1\.0\.\d+", container["image"]):
        raise ValueError("Unexpected backup image repository/version")
    if container["securityContext"] != {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}, "readOnlyRootFilesystem": True}:
        raise ValueError("Unexpected backup container permissions")
    if container["resources"] != {"requests": {"cpu": "30m", "memory": "64Mi"}, "limits": {"cpu": "500m", "memory": "192Mi"}}:
        raise ValueError("Unexpected backup container resource limits")
    mounts = [{"name": name, "mountPath": "/source/" + name, "readOnly": True} for name in ("workspace", "state", "published", "astrbot")]
    mounts += [{"name": "backup", "mountPath": "/backups"}, {"name": "script", "mountPath": "/scripts", "readOnly": True}]
    if container["volumeMounts"] != mounts:
        raise ValueError("Backup source mounts must remain read-only")
    pod["volumes"][-1]["configMap"]["name"] = configmap_name
    name = "agent-backup-manual-" + revision[:8] + "-" + uuid.uuid4().hex[:8]
    return {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "namespace": NAMESPACE,
            "labels": {"app.kubernetes.io/name": "agent-backup", "app.kubernetes.io/managed-by": "gitops-backup-runner"},
            "annotations": {"platform.lazycampus.com/revision": revision, "platform.lazycampus.com/validation-run": str(run_id)}}, "spec": spec}


def ensure_backup_idle(jobs):
    for item in jobs:
        metadata = item.get("metadata", {})
        managed = metadata.get("labels", {}).get("app.kubernetes.io/name") == "agent-backup"
        scheduled = any(owner.get("kind") == "CronJob" and owner.get("name") == "agent-backup"
                        for owner in metadata.get("ownerReferences", []))
        terminal = any(condition.get("type") in ("Complete", "Failed") and condition.get("status") == "True"
                       for condition in item.get("status", {}).get("conditions", []))
        if (managed or scheduled) and not terminal:
            raise ValueError("An Agent backup is already active; wait before creating another")


def successful_pod(job, pods):
    metadata = job["metadata"]
    candidates = []
    for pod in pods:
        owners = pod.get("metadata", {}).get("ownerReferences", [])
        owned = any(owner.get("kind") == "Job" and owner.get("name") == metadata["name"]
                    and owner.get("uid") == metadata["uid"] and owner.get("controller") is True
                    for owner in owners)
        status = pod.get("status", {})
        succeeded = any(container.get("name") == "backup"
                        and container.get("state", {}).get("terminated", {}).get("exitCode") == 0
                        for container in status.get("containerStatuses", []))
        if owned and status.get("phase") == "Succeeded" and succeeded:
            candidates.append(pod["metadata"]["name"])
    if len(candidates) != 1 or not re.fullmatch(r"[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?", candidates[0]):
        raise ValueError("Expected one successful backup Pod owned by this Job; raw logs are withheld")
    return candidates[0]


def wait_job(name, timeout=1320):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = json.loads(kube("get", "job", name, "-o", "json"))
        status = job.get("status", {})
        conditions = {item.get("type"): item.get("status") for item in status.get("conditions", [])}
        if conditions.get("Failed") == "True":
            raise RuntimeError("Agent backup Job failed; production source files remain unchanged")
        if conditions.get("Complete") == "True":
            return job
        time.sleep(5)
    raise RuntimeError("Agent backup Job wait timed out; its configured deadline/TTL remain in effect")


def verify_backup(backup_root, summary):
    """Restore only into a fresh private temporary directory; no production paths."""
    import contextlib
    import datetime
    import hashlib
    import json
    import os
    from pathlib import Path, PurePosixPath
    import re
    import shutil
    import sqlite3
    import tarfile
    import tempfile

    root = Path(backup_root).resolve(strict=True)
    name = summary.get("archive", "")
    if not re.fullmatch(r"agent-\d{8}T\d{6}Z\.tar\.gz", name) or not re.fullmatch(r"[0-9a-f]{64}", summary.get("sha256", "")):
        raise ValueError("Unexpected backup summary")
    archive_path = root / name
    if archive_path.is_symlink() or archive_path.resolve(strict=True).parent != root:
        raise ValueError("Backup archive escaped its fixed directory")
    def digest(path):
        result = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                result.update(chunk)
        return result.hexdigest()
    if archive_path.stat().st_size != summary.get("bytes") or digest(archive_path) != summary["sha256"]:
        raise ValueError("Backup archive checksum mismatch")
    record_path = root / (name + ".json")
    if record_path.is_symlink() or record_path.resolve(strict=True).parent != root:
        raise ValueError("Backup summary escaped its fixed directory")
    record = json.loads(record_path.read_text())
    if any(record.get(key) != summary.get(key) for key in ("archive", "sha256", "files", "sqlite_databases", "bytes")):
        raise ValueError("Backup Job output and saved summary disagree")
    files, sqlite_count, total = 0, 0, 0
    with tarfile.open(archive_path, "r:gz") as archive:
        members, names, unpacked_bytes = [], set(), 0
        for member in archive:
            unpacked_bytes += member.size
            if len(members) >= 200000 or member.name in names or unpacked_bytes > 8 * 1024**3 + 32 * 1024**2:
                raise ValueError("Backup archive exceeds entry/size limits or repeats entries")
            members.append(member)
            names.add(member.name)
        manifest_member = archive.getmember("agent/manifest.json")
        if not manifest_member.isfile() or manifest_member.size > 32 * 1024 * 1024:
            raise ValueError("Invalid backup manifest")
        manifest = json.load(archive.extractfile(manifest_member))
        if manifest.get("format") != 1 or not isinstance(manifest.get("files"), list):
            raise ValueError("Unsupported backup format")
        expected = {}
        for item in manifest["files"]:
            relative = item.get("path", "")
            parts = PurePosixPath(relative).parts
            if not relative or "\\" in relative or len(parts) < 2 or str(PurePosixPath(relative)) != relative or parts[0] not in ("workspace", "state", "published", "astrbot") or any(part in (".", "..") for part in parts) or PurePosixPath(relative).is_absolute():
                raise ValueError("Unsafe backup member path")
            if "agent/" + relative in expected or not isinstance(item.get("size"), int) or item["size"] < 0 or not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")) or item.get("kind") not in ("file", "sqlite-online"):
                raise ValueError("Invalid or duplicate file metadata")
            total += item["size"]
            expected["agent/" + relative] = item
        if total > 8 * 1024**3 or shutil.disk_usage(root).free < total + 256 * 1024**2:
            raise RuntimeError("Insufficient space or excessive size for isolated verification")
        if {item.name for item in members if not item.isdir()} != {*expected, "agent/manifest.json"}:
            raise ValueError("Archive contains undeclared files or links")
        with tempfile.TemporaryDirectory(prefix=".agent-verify-", dir=root) as temporary:
            destination = Path(temporary).resolve()
            destination.chmod(0o700)
            for member_name, item in expected.items():
                member = archive.getmember(member_name)
                if not member.isfile() or member.size != item["size"]:
                    raise ValueError("Backup member type/size mismatch")
                target = destination / item["path"]
                if not target.resolve().is_relative_to(destination):
                    raise ValueError("Restored path escaped verification directory")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o600)
                if digest(target) != item["sha256"]:
                    raise ValueError("Restored file checksum mismatch")
                if item["kind"] == "sqlite-online":
                    with contextlib.closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as db:
                        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                            raise ValueError("Restored SQLite quick_check failed")
                    sqlite_count += 1
                files += 1
    if files != summary["files"] or sqlite_count != summary["sqlite_databases"]:
        raise ValueError("Restored file counts do not match the backup summary")
    verification = {"verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "files_verified": files, "sqlite_verified": sqlite_count,
                    "revision": summary["revision"], "validation_run": summary["validation_run"], "job": summary["job"]}
    record["verification"] = verification
    pending = root / (name + ".verification.partial")
    try:
        pending.write_text(json.dumps(record, indent=2) + "\n")
        pending.chmod(0o600)
        os.replace(pending, record_path)
    finally:
        pending.unlink(missing_ok=True)
    return {"archive": str(archive_path), "sha256": summary["sha256"], **verification}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args()
    if args.run_id <= 0:
        raise ValueError("A positive successful validation run ID is required")
    contents = archived(args.revision)
    config = json.loads(contents["config/production.json"])
    gate(config, args.revision, args.run_id)
    cron = json.loads(kube("create", "--dry-run=client", "--validate=false", "-f", "-", "-o", "json", data=contents[APP + "backup.yaml"]))
    current = json.loads(kube("get", "cronjob", "agent-backup", "-o", "json"))
    script_volumes = [item for item in current["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"] if item["name"] == "script"]
    if len(script_volumes) != 1:
        raise ValueError("Current backup CronJob has no unique managed script")
    configmap_name = script_volumes[0]["configMap"]["name"]
    job = make_job(cron, configmap_name, args.revision, args.run_id)
    deployed_script = json.loads(kube("get", "configmap", configmap_name, "-o", "json")).get("data", {}).get("backup_agent.py", "")
    if deployed_script.encode() != contents[APP + "backup_agent.py"]:
        raise ValueError("Backup ConfigMap is not the selected production script; wait for Flux")
    gate(config, args.revision, args.run_id)
    # CronJob-generated Jobs do not inherit labels from the pod template.
    ensure_backup_idle(json.loads(kube("get", "jobs", "-o", "json"))["items"])
    name = job["metadata"]["name"]
    kube("apply", "--server-side", "--field-manager=gitops-backup-runner", "-f", "-", data=json.dumps(job).encode())
    print(f"Started managed backup Job {name} at {args.revision}.", flush=True)
    completed = wait_job(name)
    pods = json.loads(kube("get", "pods", "-l", "batch.kubernetes.io/job-name=" + name, "-o", "json"))["items"]
    pod_name = successful_pod(completed, pods)
    lines = kube("logs", "pod/" + pod_name, "-c", "backup").decode().strip().splitlines()
    if len(lines) != 1:
        raise ValueError("Expected only the backup summary; raw logs are withheld")
    summary = json.loads(lines[0])
    summary.update(revision=args.revision, validation_run=args.run_id, job=name)
    module_text = contents[SELF].decode("utf-8")
    function = next(node for node in ast.parse(module_text).body if isinstance(node, ast.FunctionDef) and node.name == "verify_backup")
    remote = ast.get_source_segment(module_text, function) + "\nimport os,json,signal\ndef verification_timeout(signum,frame):\n    raise TimeoutError('verification timed out')\nsignal.signal(signal.SIGALRM,verification_timeout)\nsignal.alarm(1150)\nos.setgroups([])\nos.setgid(1000)\nos.setuid(1000)\n"
    remote += "print(json.dumps(verify_backup(" + repr(BACKUPS) + ", json.loads(" + repr(json.dumps(summary)) + "))))\n"
    verified = json.loads(ssh(["python3", "-"], data=remote.encode(), timeout=1200))
    print(json.dumps(verified, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Managed Agent backup or isolated restore verification failed; raw logs and file contents are withheld. No live files were restored.", file=sys.stderr)
        sys.exit(1)
