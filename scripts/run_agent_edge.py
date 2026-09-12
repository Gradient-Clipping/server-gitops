#!/usr/bin/env python3
"""Apply/check Agent EdgeOne configuration from the exact validated production tree."""
import argparse
import io
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile

from production_gate import api, validate_run

ROOT = Path(__file__).resolve().parents[1]
SERVER = "root@1.14.95.189"
FILES = ("config/production.json", "scripts/run_agent_edge.py", "scripts/production_gate.py",
         "scripts/reconcile-agent-edge.py", "scripts/retire_agent_admin_domain.py", "controller/domain-reconciler/src")


def run(args, content=None):
    result = subprocess.run(args, input=content, capture_output=True, cwd=ROOT, timeout=180)
    if result.returncode:
        raise RuntimeError(f"Managed EdgeOne command failed with exit {result.returncode}; output withheld")
    return result.stdout


def gate(config, revision, run_id):
    expected = {"repository": "Gradient-Clipping/server-gitops", "sourceBranch": "main", "productionBranch": "production",
                "validationWorkflow": ".github/workflows/validate.yml", "validationJob": "validate"}
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError("Unexpected production gate configuration")
    prefix = "repos/" + config["repository"]
    validation = api(f"{prefix}/actions/runs/{run_id}")
    jobs = api(f"{prefix}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")["jobs"]
    validate_run(config, revision, validation, jobs)
    if validation.get("status") != "completed" or validation.get("conclusion") != "success":
        raise ValueError("Validation run must have completed successfully")
    if api(f"{prefix}/git/ref/heads/production")["object"]["sha"] != revision:
        raise ValueError("EdgeOne configuration requires the exact current production revision")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true", help="Apply; without this flag only verify")
    parser.add_argument("--retire-previous", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision) or args.run_id <= 0:
        raise ValueError("A complete production revision and positive validation run ID are required")
    run(["git", "fetch", "origin", "production"])
    payload = run(["git", "archive", "--format=tar", args.revision, *FILES])
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        config = json.load(archive.extractfile("config/production.json"))
        for name in ("scripts/run_agent_edge.py", "scripts/production_gate.py"):
            if (ROOT / name).read_bytes().replace(b"\r\n", b"\n") != archive.extractfile(name).read().replace(b"\r\n", b"\n"):
                raise ValueError("Runner or gate helper differs from the committed revision")
    gate(config, args.revision, args.run_id)
    remote = "/var/lib/platform-gitops/" + args.revision + "/agent-edge"
    ssh = [shutil.which("ssh.exe") or "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", SERVER]
    run(ssh + ["install -d -m 0700 " + shlex.quote(remote) + " && tar -xf - -C " + shlex.quote(remote)], payload)
    gate(config, args.revision, args.run_id)
    command = ["python3", remote + "/scripts/reconcile-agent-edge.py", "--apply" if args.apply else "--check"]
    if args.retire_previous:
        command.append("--retire-previous")
    summary = json.loads(run(ssh + [shlex.join(command)]))
    print(json.dumps({"revision": args.revision, "validation_run": args.run_id, **summary}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Managed Agent EdgeOne operation failed ({type(error).__name__}); raw output withheld", file=sys.stderr)
        sys.exit(1)
