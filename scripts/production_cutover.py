#!/usr/bin/env python3
"""Stage the one-time Flux branch migration without reconciling an older branch."""
import argparse
import datetime
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

from production_gate import api, validate_run

ROOT = Path(__file__).resolve().parents[1]
SERVER = "root@1.14.95.189"
SYNC = "clusters/easy-platform/flux-system/gotk-sync.yaml"
AUTOMATION = "clusters/easy-platform/flux-system/image-update-automation.yaml"


def run(args, data=None):
    result = subprocess.run(args, input=data, capture_output=True, text=True,
                            encoding="utf-8", timeout=90, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed with exit {result.returncode}")
    return result.stdout


def kube(args, data=None):
    return run([shutil.which("ssh.exe") or "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                SERVER, shlex.join(["k3s", "kubectl", "-n", "flux-system", *args])], data)


def get(kind, name):
    return json.loads(kube(["get", kind, name, "-o", "json"]))


def suspend(kind, name, value):
    kube(["patch", kind, name, "--type=merge", "-p", json.dumps({"spec": {"suspend": value}})])


def ref(config, branch):
    return api(f"repos/{config['repository']}/git/ref/heads/{branch}")["object"]["sha"]


def ready(resource):
    return any(c.get("type") == "Ready" and c.get("status") == "True"
               for c in resource.get("status", {}).get("conditions", []))


def validate_source(config, source):
    if source["spec"].get("url") != "ssh://git@github.com/" + config["repository"] + ".git":
        raise ValueError("Unexpected Git Source repository")


def prepare(config, checkpoint):
    if checkpoint.exists():
        raise ValueError("Checkpoint already exists; resume or cancel the recorded cutover")
    source = get("gitrepository", "flux-system")
    sync = get("kustomization", "flux-system")
    writer = get("imageupdateautomation", "platform-images")
    validate_source(config, source)
    if source["spec"].get("ref", {}).get("branch") != config["sourceBranch"]:
        raise ValueError("Cutover is only valid from the configured source branch")
    if not ready(source) or not ready(sync) or not ready(writer) or sync["spec"].get("suspend") or writer["spec"].get("suspend"):
        raise ValueError("Source, reconciliation and image writer must be healthy and active")
    baseline = ref(config, config["sourceBranch"])
    if ref(config, config["productionBranch"]) != baseline:
        raise ValueError("Wait for the current main revision to be validated and promoted")
    if not sync["status"].get("lastAppliedRevision", "").endswith(baseline):
        raise ValueError("Wait for Flux to apply the validated baseline")
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(json.dumps({"server": SERVER, "repository": config["repository"],
        "baseline": baseline, "preparedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()}, indent=2), encoding="utf-8")
    suspend("imageupdateautomation", "platform-images", True)
    suspend("kustomization", "flux-system", True)
    print("Prepared: reconciliation and the single image writer are temporarily suspended; workloads keep running.")


def validate_checkpoint(config, checkpoint):
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    if state.get("server") != SERVER or state.get("repository") != config["repository"]:
        raise ValueError("Checkpoint does not belong to this cluster and repository")
    return state


def finish(config, checkpoint, revision, run_id):
    validate_checkpoint(config, checkpoint)
    if not re.fullmatch(r"[0-9a-f]{40}", revision or "") or not run_id:
        raise ValueError("The promoted commit and successful validation run ID are required")
    prefix = "repos/" + config["repository"]
    validation = api(f"{prefix}/actions/runs/{run_id}")
    jobs = api(f"{prefix}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")["jobs"]
    validate_run(config, revision, validation, jobs)
    if validation.get("conclusion") != "success" or ref(config, config["productionBranch"]) != revision:
        raise ValueError("Production must point to this fully successful validated run")
    source = get("gitrepository", "flux-system")
    validate_source(config, source)
    run(["git", "fetch", "origin", config["productionBranch"]])
    sync = run(["git", "show", revision + ":" + SYNC])
    writer = run(["git", "show", revision + ":" + AUTOMATION])
    if not re.search(r"^    branch: production$", sync, re.M) or not re.search(r"^  suspend: false$", sync, re.M):
        raise ValueError("The validated source manifest must resume on production")
    if not re.search(r"^  suspend: false$", writer, re.M):
        raise ValueError("The validated writer manifest must explicitly resume")
    # Apply only the three already-versioned bootstrap objects. All workload,
    # receiver and host desired state continues through the production source.
    kube(["apply", "--server-side", "--field-manager=kustomize-controller", "--force-conflicts", "-f", "-"],
         sync + "\n---\n" + writer)
    current = get("gitrepository", "flux-system")
    if current["spec"].get("ref", {}).get("branch") != config["productionBranch"]:
        raise ValueError("Source branch verification failed")
    state = validate_checkpoint(config, checkpoint)
    state["appliedRevision"] = revision
    checkpoint.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"Cutover applied from validated revision {revision}; verify Flux readiness and a subsequent production webhook.")


def cancel(config, checkpoint):
    state = validate_checkpoint(config, checkpoint)
    if any(ref(config, branch) != state["baseline"] for branch in [config["sourceBranch"], config["productionBranch"]]):
        raise ValueError("Branches changed; finish the validated migration instead of resuming an older source")
    suspend("kustomization", "flux-system", False)
    suspend("imageupdateautomation", "platform-images", False)
    print("Cancelled before branch changes; the original validated source is active.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "finish", "cancel"])
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--revision")
    parser.add_argument("--run-id", type=int)
    args = parser.parse_args()
    config = json.loads((ROOT / "config/production.json").read_text(encoding="utf-8"))
    if args.phase == "prepare":
        prepare(config, args.checkpoint)
    elif args.phase == "finish":
        finish(config, args.checkpoint, args.revision, args.run_id)
    else:
        cancel(config, args.checkpoint)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
