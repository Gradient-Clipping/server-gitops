"""Provision narrowly scoped KF runtime secrets from root-only host files."""
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess


def main():
    if os.geteuid() != 0:
        raise SystemExit("Root required")
    os.umask(0o077)
    directory = Path(os.environ.get("SECRET_DIR", "/etc/platform-secrets"))

    def read(name):
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise SystemExit("Missing regular runtime secret file")
        value = path.read_text().strip()
        if not value:
            raise SystemExit("Empty runtime secret file")
        path.chmod(0o600)
        return value

    def persistent(name, generate):
        path = directory / name
        if not path.exists():
            with path.open("x") as stream:
                stream.write(generate())
        return read(name)

    def apply(namespace, name, values):
        body = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": namespace},
                "type": "Opaque", "data": {k: base64.b64encode(v.encode()).decode() for k,v in values.items()}}
        result = subprocess.run(["k3s", "kubectl", "apply", "-f", "-"], input=json.dumps(body).encode(), capture_output=True)
        if result.returncode:
            raise SystemExit("Failed to apply runtime secret (details suppressed)")

    data_key = persistent("wecom-kf-data-key", lambda: base64.urlsafe_b64encode(os.urandom(32)).decode())
    oidc = persistent("wecom-kf-oidc-secret", lambda: secrets.token_urlsafe(48))
    session = persistent("wecom-kf-session-secret", lambda: secrets.token_urlsafe(48))
    deepseek = json.loads(read("wecom-kf-deepseek.json"))
    permitted = {"DEEPSEEK_API_KEY", "DEEPSEEK_MODEL_NAME", "DEEPSEEK_BASE_URL", "DEEPSEEK_TIMEOUT", "DEEPSEEK_MAX_TOKENS", "DEEPSEEK_THINKING"}
    if not deepseek.get("DEEPSEEK_API_KEY") or set(deepseek) - permitted:
        raise SystemExit("Invalid DeepSeek runtime fields")
    apply("wecom-kf", "educoder-wecom-runtime", {
        "WECOM_CORP_ID": read("educoder-wecom-corp-id"),
        "WECOM_CALLBACK_TOKEN": read("educoder-wecom-callback-token"),
        "WECOM_ENCODING_AES_KEY": read("educoder-wecom-aes-key"),
        "DATA_ENCRYPTION_KEY": data_key,
    })
    apply("wecom-kf", "wecom-kf-api", {"WECOM_API_SECRET": read("wecom-kf-api-secret")})
    apply("wecom-kf", "wecom-kf-deepseek", deepseek)
    apply("wecom-kf", "wecom-kf-admin", {"OIDC_CLIENT_SECRET": oidc, "ADMIN_SESSION_SECRET": session})
    apply("identity-system", "wecom-kf-oidc-secret", {"OIDC_CLIENT_SECRET": oidc})
    print("WeCom KF runtime secrets provisioned; secret values suppressed.")


if __name__ == "__main__":
    main()
