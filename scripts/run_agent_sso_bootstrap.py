"""Provision Agent SSO credentials from the current, CI-validated production commit."""
import argparse
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile

from production_gate import api, validate_run

ROOT = Path(__file__).resolve().parents[1]
SELF = "scripts/run_agent_sso_bootstrap.py"
FILES = (SELF, "scripts/production_gate.py", "config/production.json", "scripts/bootstrap_agent_sso.py")


def run(args, content=None):
    result = subprocess.run(args, input=content, capture_output=True, cwd=ROOT, timeout=180)
    if result.returncode:
        raise RuntimeError("Agent SSO bootstrap command failed; output withheld")
    return result.stdout


def gate(config, revision, run_id):
    expected = {"repository": "Gradient-Clipping/server-gitops", "sourceBranch": "main", "productionBranch": "production",
                "validationWorkflow": ".github/workflows/validate.yml", "validationJob": "validate"}
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError("Unexpected production gate")
    prefix = "repos/" + config["repository"]
    validation = api(f"{prefix}/actions/runs/{run_id}")
    jobs = api(f"{prefix}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")["jobs"]
    validate_run(config, revision, validation, jobs)
    if validation.get("status") != "completed" or validation.get("conclusion") != "success":
        raise ValueError("Successful completed validation required")
    if api(f"{prefix}/git/ref/heads/production")["object"]["sha"] != revision:
        raise ValueError("Only the exact current production revision is accepted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision) or args.run_id <= 0:
        raise ValueError("Exact revision and positive validation run required")
    run(["git", "fetch", "origin", "production"])
    payload = run(["git", "archive", "--format=tar", args.revision, *FILES])
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        config = json.load(archive.extractfile("config/production.json"))
        for name in (SELF, "scripts/production_gate.py"):
            if (ROOT / name).read_bytes().replace(b"\r\n", b"\n") != archive.extractfile(name).read().replace(b"\r\n", b"\n"):
                raise ValueError("Runner differs from the selected commit")
    gate(config, args.revision, args.run_id)
    remote = "/var/lib/platform-gitops/" + args.revision + "/agent-sso"
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "root@1.14.95.189"]
    run(ssh + ["install -d -m 0700 " + shlex.quote(remote) + " && tar -xf - -C " + shlex.quote(remote)], payload)
    gate(config, args.revision, args.run_id)
    result = json.loads(run(ssh + [shlex.join(["python3", remote + "/scripts/bootstrap_agent_sso.py"])]))
    print(json.dumps({**result, "revision": args.revision, "validation_run": args.run_id}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Agent SSO bootstrap stopped ({type(error).__name__}); raw output withheld", file=sys.stderr)
        sys.exit(1)
