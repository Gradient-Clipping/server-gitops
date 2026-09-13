#!/usr/bin/env python3
"""Provision shared Tailscale only after the legacy state is no longer mounted.

Run on the production host via run_shared_tailscale.py from a validated revision.
Workloads remain managed by Flux. Credentials and device state never leave the host.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "tailscale-system"
LEGACY = Path("/srv/k3s-data/easy-swu/tailscale")
STATE = Path("/srv/k3s-data/tailscale")
BACKUPS = Path("/var/backups/platform-tailscale")
SECRETS = Path("/etc/platform-secrets")


def command(args, content=None):
    result = subprocess.run(args, input=content, text=True, capture_output=True, timeout=60)
    if result.returncode:
        # kubectl errors can echo submitted Secret data; never repeat their output.
        raise RuntimeError(f"{Path(args[0]).name} failed (exit {result.returncode}); output withheld")
    return result.stdout


def kube(*args, content=None):
    return command(["k3s", "kubectl", *args], content)


def uses_legacy_state(spec):
    return any(v.get("persistentVolumeClaim", {}).get("claimName") == "easy-swu-tailscale"
               or v.get("hostPath", {}).get("path") == str(LEGACY)
               for v in spec.get("volumes", []))


def assert_legacy_stopped(deployments, pods):
    for deployment in deployments:
        # Even a zero-replica legacy template could recreate the old writer later.
        if uses_legacy_state(deployment.get("spec", {}).get("template", {}).get("spec", {})):
            raise ValueError("Wait for Flux to remove the legacy Tailscale deployment template")
    for pod in pods:
        if uses_legacy_state(pod.get("spec", {})):
            raise ValueError("Wait for every legacy state-mounting Pod to be deleted, including terminating Pods")


def check_path(path):
    if path.resolve() != path or path.is_symlink():
        raise ValueError("Refusing a redirected state path")


def valid_state(path):
    state = path / "tailscaled.state"
    if not state.is_file() or state.is_symlink():
        return False
    try:
        payload = json.loads(state.read_text())
    except (ValueError, OSError):
        return False
    return isinstance(payload, dict) and bool(payload.get("_machinekey"))


def secure_tree(path):
    for item in [path, *path.rglob("*")]:
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ValueError("State contains an unexpected link or special file")
        os.chown(item, 1000, 1000)
        item.chmod(0o700 if item.is_dir() else 0o600)


def prepare_state(source, target, backups, fresh=False):
    for path in (source, target, backups):
        check_path(path)
    if target.exists():
        if not valid_state(target):
            raise ValueError("Existing shared state is incomplete; refusing to overwrite or enroll again")
        return "existing"
    if source.exists() and not valid_state(source):
        raise ValueError("Legacy state is invalid; refusing a new device enrollment")
    if not source.exists() and not fresh:
        raise ValueError("No device state exists; fresh installations require --fresh-enrollment")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".tailscale-staging-", dir=target.parent))
    try:
        if source.exists():
            # Source is offline. Retain both an immutable backup and its original path.
            for item in source.rglob("*"):
                if item.is_symlink() or not (item.is_file() or item.is_dir()):
                    raise ValueError("Legacy state contains an unexpected link or special file")
            backups.mkdir(parents=True, exist_ok=True, mode=0o700)
            backup = backups / datetime.now(timezone.utc).strftime("legacy-%Y%m%dT%H%M%S%fZ")
            shutil.copytree(source, backup)
            shutil.copytree(backup, staging, dirs_exist_ok=True)
            if (source / "tailscaled.state").read_bytes() != (staging / "tailscaled.state").read_bytes():
                raise ValueError("Device state copy verification failed")
        secure_tree(staging)
        # Publish the directory atomically: hostPath Directory cannot mount partial state.
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return "migrated" if source.exists() else "fresh"


def read_secret(name):
    path = SECRETS / name
    value = path.read_text().strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError(f"Invalid secret file: {name}")
    path.chmod(0o600)
    return value


def apply_secret(name, values, secret_type="Opaque"):
    resource = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": NAMESPACE},
                "type": secret_type, "data": {key: base64.b64encode(value.encode()).decode() for key, value in values.items()}}
    kube("apply", "--server-side", "--field-manager=tailscale-bootstrap", "-f", "-", content=json.dumps(resource))


def bootstrap(fresh=False):
    deadline = time.monotonic() + 240
    while True:
        deployments = json.loads(kube("-n", "easy-swu", "get", "deployments", "-o", "json"))["items"]
        pods = json.loads(kube("-n", "easy-swu", "get", "pods", "-o", "json"))["items"]
        try:
            assert_legacy_stopped(deployments, pods)
            break
        except ValueError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(3)
    # Read and validate prerequisites before publishing the shared state directory.
    key_path = SECRETS / "tailscale-auth-key"
    key = read_secret("tailscale-auth-key" if key_path.exists() else "easy-swu-tailscale-auth-key")
    username, password = read_secret("tcr-username"), read_secret("tcr-password")
    status = prepare_state(LEGACY, STATE, BACKUPS, fresh)
    if not key_path.exists():
        with key_path.open("x") as stream:
            stream.write(key + "\n")
        key_path.chmod(0o600)
    kube("apply", "--server-side", "--field-manager=tailscale-bootstrap", "-f",
         str(ROOT / "clusters/easy-platform/infrastructure/tailscale/namespace.yaml"))
    registry = {"auths": {"ccr.ccs.tencentyun.com": {"username": username, "password": password,
                "auth": base64.b64encode(f"{username}:{password}".encode()).decode()}}}
    apply_secret("tcr-auth", {".dockerconfigjson": json.dumps(registry)}, "kubernetes.io/dockerconfigjson")
    apply_secret("tailscale-auth", {"TS_AUTHKEY": key})
    print(f"Shared Tailscale prerequisites ready; state={status}. No device credentials were printed.")


def main():
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh-enrollment", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError("Run the host bootstrap as root")
    os.umask(0o077)
    with open("/run/lock/platform-tailscale-bootstrap.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        bootstrap(args.fresh_enrollment)


if __name__ == "__main__":
    main()
