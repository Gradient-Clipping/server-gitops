#!/usr/bin/env python3
"""Review, then retire idle verification sessions without deleting history or files."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

from production_gate import api, validate_run

ROOT = Path(__file__).resolve().parents[1]
SELF = "scripts/run_agent_verification_retire.py"
FILES = (SELF, "scripts/production_gate.py", "config/production.json")


def command(args, data=None):
    try:
        result = subprocess.run(args, input=data, capture_output=True, cwd=ROOT,
                                timeout=90, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Verification retirement command timed out") from None
    if result.returncode:
        raise RuntimeError(f"Verification retirement command failed with exit {result.returncode}; output withheld")
    return result.stdout


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
    expected = {"repository": "Gradient-Clipping/server-gitops", "sourceBranch": "main",
                "productionBranch": "production", "validationWorkflow": ".github/workflows/validate.yml",
                "validationJob": "validate"}
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError("Unexpected production gate configuration")
    prefix = "repos/" + config["repository"]
    run = api(f"{prefix}/actions/runs/{run_id}")
    jobs = api(f"{prefix}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")["jobs"]
    validate_run(config, revision, run, jobs)
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ValueError("Validation run must have completed successfully")
    if api(f"{prefix}/git/ref/heads/production")["object"]["sha"] != revision:
        raise ValueError("Retirement requires the exact current production revision")


def retirement(db, revision, run_id, session_ids, approved=None):
    """Self-contained, archived implementation; caller owns the database transaction."""
    import json
    import re
    import sqlite3

    if not session_ids or len(set(session_ids)) != len(session_ids) or any(not re.fullmatch(r"ses_[A-Za-z0-9]+", item) for item in session_ids):
        raise ValueError("Explicit, distinct OpenCode session IDs are required")
    db.row_factory = sqlite3.Row
    sessions = {row["id"]: dict(row) for row in db.execute(
        "SELECT id,owner,created,updated FROM sessions ORDER BY id")}
    routes = {row["id"]: dict(row) for row in db.execute(
        "SELECT id,channel,session_id,selection,updated FROM routes ORDER BY id")}
    jobs = [dict(row) for row in db.execute(
        "SELECT id,route,session_id,status,created,updated FROM jobs ORDER BY id")]
    for route in routes.values():
        route["selection"] = json.loads(route["selection"])
        if not isinstance(route["selection"], list) or any(not isinstance(x, str) for x in route["selection"]):
            raise ValueError("Invalid route selection; references cannot be verified")

    def verification(route):
        return route is not None and route["channel"] == "verification" and route["id"].startswith("verification:")

    if not set(session_ids) <= sessions.keys():
        raise ValueError("A requested session is absent from the catalog")
    eligible, related = [], set()
    for key in sorted(session_ids):
        session = sessions[key]
        if session["owner"] != "owner":
            raise ValueError("Requested session does not have the expected current owner")
        history = [job for job in jobs if job["session_id"] == session["id"]]
        if not any(verification(routes.get(job["route"])) for job in history):
            raise ValueError("Requested session has no verification job history")
        references = {job["route"] for job in history}
        references.update(route["id"] for route in routes.values()
                          if route["session_id"] == session["id"] or session["id"] in route["selection"])
        # Check jobs on the entire affected route, including jobs not assigned a session yet.
        idle = all(job["status"] in ("completed", "failed") for job in jobs
                   if job["route"] in references or job["session_id"] == session["id"])
        if not idle or not all(verification(routes.get(route)) for route in references):
            raise ValueError("Requested session has a real, missing, or active route reference")
        eligible.append(session)
        related.update(references)

    ids = {session["id"] for session in eligible}
    # A related verification route must not point at a session outside this exact plan.
    # In particular, never detach a real conversation resumed from a test route.
    if any(routes[route]["session_id"] is not None and routes[route]["session_id"] not in ids for route in related):
        raise ValueError("Verification route currently selects a session outside the retirement plan")
    plan = {"format": 1, "operation": "retire-agent-verification-sessions",
            "namespace": "lazycampus-agent", "database": "/state/agent.sqlite3",
            "revision": revision, "validation_run": run_id,
            "session_ids": sorted(session_ids),
            "sessions": eligible, "routes": [routes[key] for key in sorted(related)],
            "jobs": [job for job in jobs if job["route"] in related or job["session_id"] in ids]}
    if approved is None:
        return plan
    if approved != plan:
        raise ValueError("Reviewed plan is stale or changed; generate and review a new dry run")
    if not ids:
        raise ValueError("Reviewed plan contains no eligible verification sessions")
    for session in eligible:
        cursor = db.execute("UPDATE sessions SET owner='verification' WHERE id=? AND owner='owner'", (session["id"],))
        if cursor.rowcount != 1:
            raise ValueError("Session changed while applying the reviewed plan")
    for route in sorted(related):
        cursor = db.execute("UPDATE routes SET session_id=NULL,selection='[]' WHERE id=? AND channel='verification'", (route,))
        if cursor.rowcount != 1:
            raise ValueError("Route changed while applying the reviewed plan")
    return {"applied": True, "retired_sessions": len(ids), "cleared_verification_routes": len(related)}


def remote_source(source, revision, run_id, session_ids, approved=None):
    tree = ast.parse(source)
    function = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "retirement")
    implementation = ast.get_source_segment(source, function)
    payload = json.dumps({"revision": revision, "run_id": run_id, "session_ids": session_ids, "approved": approved})
    return ("import json,sqlite3,sys\n" + implementation + "\n" +
            "args=json.loads(" + repr(payload) + ")\n" +
            "try:\n"
            "    mode='ro' if args['approved'] is None else 'rw'\n"
            "    with sqlite3.connect('file:/state/agent.sqlite3?mode='+mode,uri=True,timeout=30) as db:\n"
            "        if mode=='ro': db.execute('PRAGMA query_only=ON')\n"
            "        db.execute('BEGIN' if mode=='ro' else 'BEGIN IMMEDIATE')\n"
            "        result=retirement(db,**args)\n"
            "    print(json.dumps(result,sort_keys=True))\n"
            "except Exception as error:\n"
            "    print(json.dumps({'error_type':type(error).__name__}),file=sys.stderr)\n"
            "    sys.exit(1)\n").encode("utf-8")


def reviewed_plan(path, digest, revision, run_id, session_ids):
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        raise ValueError("Apply requires the reviewed plan's exact SHA-256")
    payload = path.read_bytes()
    if len(payload) > 4 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("Reviewed plan file is oversized or its SHA-256 changed")
    plan = json.loads(payload)
    if not isinstance(plan, dict) or plan.get("revision") != revision or plan.get("validation_run") != run_id or plan.get("session_ids") != sorted(session_ids):
        raise ValueError("Reviewed plan does not match this production revision and validation run")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--session-id", required=True, action="append", help="Exact verification session ID; repeat for each reviewed session")
    parser.add_argument("--plan", required=True, type=Path, help="New dry-run plan file, or the reviewed file for --apply")
    parser.add_argument("--apply", action="store_true", help="Apply only the unchanged reviewed plan after a fresh database check")
    parser.add_argument("--plan-sha256", help="Required with --apply; SHA-256 printed by the reviewed dry run")
    args = parser.parse_args()
    if args.run_id <= 0 or (not args.apply and args.plan_sha256):
        parser.error("Use a positive validation run ID and --plan-sha256 only with --apply")
    if not args.apply and args.plan.exists():
        parser.error("Dry run requires a new plan path; existing reviewed plans are never overwritten")
    approved = reviewed_plan(args.plan, args.plan_sha256, args.revision, args.run_id, args.session_id) if args.apply else None
    contents = archived(args.revision)
    gate(json.loads(contents["config/production.json"]), args.revision, args.run_id)
    payload = remote_source(contents[SELF].decode("utf-8"), args.revision, args.run_id, args.session_id, approved)
    executable = shutil.which("ssh.exe") or shutil.which("ssh") or "ssh"
    result = json.loads(command([executable, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                                "root@1.14.95.189", "k3s", "kubectl", "-n", "lazycampus-agent",
                                "exec", "-i", "deployment/agent-backend", "--", "python", "-"], data=payload))
    if args.apply:
        print(json.dumps({**result, "revision": args.revision, "plan_sha256": args.plan_sha256}, sort_keys=True))
    else:
        plan_bytes = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8")
        with args.plan.open("xb") as stream:
            stream.write(plan_bytes)
        print(json.dumps({"dry_run": True, "eligible_sessions": len(result["sessions"]),
                          "verification_routes": len(result["routes"]),
                          "plan_sha256": hashlib.sha256(plan_bytes).hexdigest()}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, KeyError, tarfile.TarError) as error:
        print(f"Verification retirement stopped ({type(error).__name__}); details withheld", file=sys.stderr)
        sys.exit(1)
