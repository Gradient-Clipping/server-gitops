#!/usr/bin/env python3
"""Bootstrap and verify shared Tailscale from the CI-validated production revision."""
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


def run(args, data=None, binary=False, timeout=120):
    result = subprocess.run(args, input=data, capture_output=True, text=not binary,
                            **({} if binary else {"encoding": "utf-8"}), timeout=timeout, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed (exit {result.returncode}); output withheld")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--expected-device-id")
    parser.add_argument("--fresh-enrollment", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("A full GitOps revision is required")
    if not args.fresh_enrollment and not args.expected_device_id:
        parser.error("Preserving an existing identity requires --expected-device-id from the baseline")
    config = json.loads((ROOT / "config/production.json").read_text())
    prefix = "repos/" + config["repository"]
    validation = api(f"{prefix}/actions/runs/{args.run_id}")
    jobs = api(f"{prefix}/actions/runs/{args.run_id}/jobs?filter=latest&per_page=100")["jobs"]
    validate_run(config, args.revision, validation, jobs)
    if validation.get("conclusion") != "success" or api(f"{prefix}/git/ref/heads/production")["object"]["sha"] != args.revision:
        raise ValueError("Bootstrap requires the current successfully validated production revision")
    run(["git", "fetch", "origin", "production"])
    for name in ("scripts/run_shared_tailscale.py", "scripts/production_gate.py", "config/production.json"):
        expected = run(["git", "show", args.revision + ":" + name], binary=True)
        if (ROOT / name).read_bytes().replace(b"\r\n", b"\n") != expected.replace(b"\r\n", b"\n"):
            raise ValueError("Local runner or gate differs from the validated production commit")
    archive = run(["git", "archive", "--format=tar", args.revision,
                   "scripts/bootstrap_shared_tailscale.py", "scripts/verify_shared_tailscale.py",
                   "clusters/easy-platform/infrastructure/tailscale/namespace.yaml"], binary=True)
    remote = "/var/lib/platform-gitops/" + args.revision
    ssh = [shutil.which("ssh.exe") or "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", SERVER]
    run(ssh + ["install -d -m 0700 " + shlex.quote(remote) + " && tar -xf - -C " + shlex.quote(remote)],
        archive, binary=True)
    if api(f"{prefix}/git/ref/heads/production")["object"]["sha"] != args.revision:
        raise ValueError("Production changed before bootstrap; use its new validated revision")
    if not args.verify_only:
        command = ["python3", remote + "/scripts/bootstrap_shared_tailscale.py"]
        if args.fresh_enrollment:
            command.append("--fresh-enrollment")
        print("Waiting for the versioned API cutover, then preparing offline device state.", flush=True)
        print(run(ssh + [shlex.join(command)], timeout=360).strip(), flush=True)
    command = ["python3", remote + "/scripts/verify_shared_tailscale.py"]
    if args.expected_device_id:
        command += ["--expected-device-id", args.expected_device_id]
    print("Verifying the gateway, API access and cross-namespace access controls.", flush=True)
    print(run(ssh + [shlex.join(command)], timeout=420).strip(), flush=True)


if __name__ == "__main__":
    main()
