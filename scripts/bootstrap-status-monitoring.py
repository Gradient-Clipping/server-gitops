#!/usr/bin/env python3
"""Restore optional, per-application monitoring credentials without printing them."""
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess

APPS = {
    "easy-swu": ("easy-swu", "api", 3000),
    "platform": ("open-platform", "platform", 3000),
    "shop": ("smart-shop", "smart-shop-backend", 9099),
    "identity": ("identity-system", "identity-bridge", 3000),
}


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run as root on the existing cluster host")
    os.umask(0o077)
    directory = Path(os.environ.get("SECRET_DIR", "/etc/platform-secrets"))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    credentials = {}
    items = []
    for name, (namespace, service, port) in APPS.items():
        path = directory / f"status-monitor-{name}"
        if not path.exists():
            path.write_text(secrets.token_urlsafe(48))
        path.chmod(0o600)
        token = path.read_text().strip()
        if len(token) < 32:
            raise SystemExit("Invalid stored monitoring credential")
        credentials[name] = {"token": token, "target": f"http://{service}.{namespace}.svc.cluster.local:{port}/internal/monitoring/v1/state"}
        items.append({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "status-monitor", "namespace": namespace}, "type": "Opaque", "data": {"STATUS_MONITOR_TOKEN": base64.b64encode(token.encode()).decode()}})
    items.append({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "status-probes", "namespace": "status-page"}, "type": "Opaque", "data": {"STATUS_PROBE_CREDENTIALS": base64.b64encode(json.dumps(credentials).encode()).decode()}})
    result = subprocess.run(["k3s", "kubectl", "apply", "-f", "-"], input=json.dumps({"apiVersion": "v1", "kind": "List", "items": items}), text=True, capture_output=True)
    if result.returncode:
        raise SystemExit("Monitoring credential reconciliation failed")
    print("Monitoring credentials reconciled for four applications; values were not displayed.")


if __name__ == "__main__":
    main()
