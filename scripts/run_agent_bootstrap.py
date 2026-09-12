#!/usr/bin/env python3
"""Apply Agent bootstrap from the exact CI-validated current production tree."""
import argparse
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess

from production_gate import api, validate_run

ROOT = Path(__file__).resolve().parents[1]
SERVER = "root@1.14.95.189"


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
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--restart-k3s", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise ValueError("A full GitOps revision is required")
    config = json.loads((ROOT / "config/production.json").read_text())
    prefix = "repos/" + config["repository"]
    validation = api(f"{prefix}/actions/runs/{args.run_id}")
    jobs = api(f"{prefix}/actions/runs/{args.run_id}/jobs?filter=latest&per_page=100")["jobs"]
    validate_run(config, args.revision, validation, jobs)
    if validation.get("conclusion") != "success" or api(f"{prefix}/git/ref/heads/production")["object"]["sha"] != args.revision:
        raise ValueError("Bootstrap requires the current successfully validated production revision")
    run(["git", "fetch", "origin", "production"])
    archive = run(["git", "archive", "--format=tar", args.revision,
                   "scripts/bootstrap_agent.py", "host/agent", "host/k3s/config.yaml", "config/agent-views.sql"], binary=True)
    remote = "/var/lib/platform-gitops/" + args.revision
    ssh = [shutil.which("ssh.exe") or "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", SERVER]
    run(ssh + ["install -d -m 0700 " + shlex.quote(remote) + " && tar -xf - -C " + shlex.quote(remote)], archive, binary=True)
    env_target = "/etc/platform-secrets/lazycampus-agent.env"
    run(ssh + ["umask 077; cat > " + shlex.quote(env_target)], args.env_file.read_bytes(), binary=True)
    command = ["python3", remote + "/scripts/bootstrap_agent.py", "--env-file", env_target]
    if args.restart_k3s:
        command.append("--restart-k3s")
    result = run(ssh + [shlex.join(command)], timeout=1200)
    print(result.strip())


if __name__ == "__main__":
    main()
