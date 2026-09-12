"""Provision the dedicated SSO client secret; execute only through the gated runner."""
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess

PATH = Path("/etc/platform-secrets/agent-admin-oidc-client-secret")
NAMESPACES = ("identity-system", "lazycampus-agent")
NAME = "agent-admin-oidc-secret"


def command(args, content=None):
    result = subprocess.run(args, input=content, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError("SSO secret provisioning command failed; output withheld")
    return result.stdout


def main():
    if PATH.is_symlink():
        raise ValueError("SSO secret must be a regular managed file")
    if not PATH.exists():
        descriptor = os.open(PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(secrets.token_urlsafe(48) + "\n")
    value = PATH.read_text().strip()
    if len(value) < 48 or not PATH.is_file():
        raise ValueError("Invalid managed SSO secret")
    PATH.chmod(0o600)
    encoded = base64.b64encode(value.encode()).decode()
    changed = 0
    for namespace in NAMESPACES:
        raw = command(["k3s", "kubectl", "get", "secret", NAME, "-n", namespace, "--ignore-not-found", "-o", "json"])
        if raw.strip():
            if json.loads(raw).get("data", {}).get("OIDC_CLIENT_SECRET") != encoded:
                raise ValueError("Existing SSO Secret differs from the retained recovery file; explicit rotation is required")
            continue
        resource = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": NAME, "namespace": namespace,
                    "labels": {"app.kubernetes.io/managed-by": "agent-sso-bootstrap"}},
                    "type": "Opaque", "data": {"OIDC_CLIENT_SECRET": encoded}}
        command(["k3s", "kubectl", "create", "-f", "-"], json.dumps(resource))
        changed += 1
    print(json.dumps({"ready": True, "created_secrets": changed}))


if __name__ == "__main__":
    main()
