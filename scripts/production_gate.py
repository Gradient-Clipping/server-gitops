#!/usr/bin/env python3
"""Promote only the exact, current revision with a successful validation job."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


class ApiError(RuntimeError):
    def __init__(self, code):
        super().__init__(f"GitHub request failed ({code})")
        self.code = code


def api(path, method="GET", payload=None):
    args = ["gh", "api", "--method", method, path]
    if payload is not None:
        args += ["--input", "-"]
    result = subprocess.run(args, input=None if payload is None else json.dumps(payload),
                            text=True, encoding="utf-8", capture_output=True, timeout=60)
    if result.returncode:
        # Never repeat response bodies or credentials into a workflow log.
        match = re.search(r"HTTP (\d{3})", result.stderr)
        raise ApiError(int(match[1]) if match else result.returncode)
    return json.loads(result.stdout) if result.stdout.strip() else None


def validate_run(config, revision, run, jobs, attempt=None):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("A complete commit SHA is required")
    if (run.get("head_sha") != revision
            or run.get("head_branch") != config["sourceBranch"]
            or run.get("event") not in ("push", "workflow_dispatch")
            or run.get("path", "").split("@")[0] != config["validationWorkflow"]
            or run.get("head_repository", {}).get("full_name") != config["repository"]):
        raise ValueError("Validation run does not match the trusted source revision")
    if attempt is not None and run.get("run_attempt") != attempt:
        raise ValueError("Validation attempt changed")
    required = [j for j in jobs if j.get("name") == config["validationJob"]]
    if len(required) != 1 or required[0].get("conclusion") != "success":
        raise ValueError("The required validation job has not succeeded")


def promote(config, revision, run_id, attempt=None):
    prefix = "repos/" + config["repository"]
    run = api(f"{prefix}/actions/runs/{run_id}")
    jobs = api(f"{prefix}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")["jobs"]
    validate_run(config, revision, run, jobs, attempt)
    source = api(f"{prefix}/git/ref/heads/{config['sourceBranch']}")["object"]["sha"]
    if source != revision:
        print("Skipped: a newer source commit is waiting for validation.")
        return "stale"
    path = f"{prefix}/git/refs/heads/{config['productionBranch']}"
    try:
        previous = api(f"{prefix}/git/ref/heads/{config['productionBranch']}")["object"]["sha"]
    except ApiError as error:
        if error.code != 404:
            raise
        api(f"{prefix}/git/refs", "POST", {"ref": "refs/heads/" + config["productionBranch"], "sha": revision})
        print(f"Created {config['productionBranch']} at validated revision {revision}.")
        return "created"
    if previous == revision:
        print("Production already matches the validated revision.")
        return "unchanged"
    comparison = api(f"{prefix}/compare/{previous}...{revision}")
    if comparison.get("status") != "ahead":
        raise ValueError("Production must advance by fast-forward; force updates are forbidden")
    api(path, "PATCH", {"sha": revision, "force": False})
    print(f"Promoted {config['productionBranch']} to validated revision {revision}.")
    return "promoted"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--run-id", type=int, default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--attempt", type=int, default=os.environ.get("GITHUB_RUN_ATTEMPT"))
    args = parser.parse_args()
    if not args.revision or not args.run_id:
        parser.error("A revision and validation run ID are required")
    config = json.loads((ROOT / "config/production.json").read_text(encoding="utf-8"))
    promote(config, args.revision, args.run_id, args.attempt)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
