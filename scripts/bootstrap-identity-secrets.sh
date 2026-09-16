#!/usr/bin/env bash
set -euo pipefail

umask 077

KUBECTL="${KUBECTL:-k3s kubectl}"
SECRET_DIR="${SECRET_DIR:-/etc/platform-secrets}"
REGISTRY="ccr.ccs.tencentyun.com"
NAMESPACE="identity-system"

install -d -m 0700 "${SECRET_DIR}"

require_file() {
  if [[ ! -s "$1" ]]; then
    echo "Required secret file is missing or empty: $1" >&2
    exit 1
  fi
}

generate_hex_file() {
  local path="$1"
  local bytes="$2"
  if [[ ! -s "${path}" ]]; then
    openssl rand -hex "${bytes}" >"${path}"
  fi
  chmod 0600 "${path}"
}

write_fixed_file() {
  local path="$1"
  local value="$2"
  if [[ ! -s "${path}" ]]; then
    printf '%s' "${value}" >"${path}"
  fi
  chmod 0600 "${path}"
}

require_file "${SECRET_DIR}/tcr-username"
require_file "${SECRET_DIR}/tcr-password"
require_file "${SECRET_DIR}/smart-shop.env"
require_file "${SECRET_DIR}/keycloak-ystemsrx-password"
require_file "${SECRET_DIR}/keycloak-additional-platform-admin-password"
require_file "${SECRET_DIR}/keycloak-zadmin-platform-admin-password"
require_file "${SECRET_DIR}/keycloak-zjx-platform-admin-password"

write_fixed_file "${SECRET_DIR}/keycloak-bootstrap-admin-username" "platform-bootstrap-admin"
write_fixed_file "${SECRET_DIR}/keycloak-platform-admin-username" "platform-admin"
generate_hex_file "${SECRET_DIR}/keycloak-bootstrap-admin-password" 24
generate_hex_file "${SECRET_DIR}/keycloak-platform-admin-password" 24
generate_hex_file "${SECRET_DIR}/identity-bridge-oidc-client-secret" 32
generate_hex_file "${SECRET_DIR}/smart-shop-oidc-client-secret" 32
generate_hex_file "${SECRET_DIR}/headlamp-oidc-client-secret" 32
generate_hex_file "${SECRET_DIR}/easy-swu-admin-oidc-client-secret" 32
generate_hex_file "${SECRET_DIR}/smart-shop-login-api-token" 32
generate_hex_file "${SECRET_DIR}/easy-swu-identity-sync-token" 32
generate_hex_file "${SECRET_DIR}/identity-bridge-cookie-key-current" 32
generate_hex_file "${SECRET_DIR}/identity-bridge-cookie-key-previous" 32

if [[ ! -s "${SECRET_DIR}/identity-bridge-jwks.json" ]]; then
  node >"${SECRET_DIR}/identity-bridge-jwks.json" <<'NODE'
const crypto = require("node:crypto");
const { privateKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 3072 });
const jwk = privateKey.export({ format: "jwk" });
jwk.use = "sig";
jwk.alg = "RS256";
jwk.kid = crypto.randomUUID();
process.stdout.write(JSON.stringify({ keys: [jwk] }));
NODE
fi
chmod 0600 "${SECRET_DIR}/identity-bridge-jwks.json"

${KUBECTL} create namespace "${NAMESPACE}" --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null
${KUBECTL} label namespace "${NAMESPACE}" \
  app.kubernetes.io/part-of=identity-platform \
  pod-security.kubernetes.io/enforce=restricted \
  --overwrite >/dev/null
${KUBECTL} create namespace smart-shop --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null
${KUBECTL} label namespace smart-shop \
  platform.lazycampus.com/identity-client=true \
  --overwrite >/dev/null
${KUBECTL} create namespace headlamp-system --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null
${KUBECTL} label namespace headlamp-system \
  app.kubernetes.io/part-of=headlamp \
  pod-security.kubernetes.io/enforce=restricted \
  --overwrite >/dev/null

docker_config="$(mktemp)"
bridge_env="$(mktemp)"
keycloak_env="$(mktemp)"
headlamp_env="$(mktemp)"
cleanup() {
  rm -f -- "${docker_config}" "${bridge_env}" "${keycloak_env}" "${headlamp_env}"
}
trap cleanup EXIT

python3 - "${docker_config}" "${SECRET_DIR}" "${REGISTRY}" <<'PY'
import base64
import json
import pathlib
import sys

output_path = pathlib.Path(sys.argv[1])
secret_dir = pathlib.Path(sys.argv[2])
registry = sys.argv[3]
username = (secret_dir / "tcr-username").read_text(encoding="utf-8").strip()
password = (secret_dir / "tcr-password").read_text(encoding="utf-8").strip()
auth = base64.b64encode(f"{username}:{password}".encode()).decode()
output_path.write_text(
    json.dumps(
        {"auths": {registry: {"username": username, "password": password, "auth": auth}}},
        separators=(",", ":"),
    ),
    encoding="utf-8",
)
PY

for namespace in "${NAMESPACE}" headlamp-system; do
  ${KUBECTL} --namespace "${namespace}" create secret generic tcr-auth \
    --type=kubernetes.io/dockerconfigjson \
    --from-file=.dockerconfigjson="${docker_config}" \
    --dry-run=client -o yaml \
    | ${KUBECTL} apply -f - >/dev/null
done

provision_script="/usr/local/sbin/provision-mysql-database"
if [[ ! -x "${provision_script}" ]]; then
  provision_script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/provision-mysql-database.sh"
fi
"${provision_script}" keycloak "${NAMESPACE}" mysql-keycloak keycloak_app
"${provision_script}" identity_bridge "${NAMESPACE}" mysql-identity-bridge identity_bridge_app

python3 - "${bridge_env}" "${keycloak_env}" "${headlamp_env}" "${SECRET_DIR}" <<'PY'
import pathlib
import sys

bridge_path = pathlib.Path(sys.argv[1])
keycloak_path = pathlib.Path(sys.argv[2])
headlamp_path = pathlib.Path(sys.argv[3])
secret_dir = pathlib.Path(sys.argv[4])

def read(name: str) -> str:
    value = (secret_dir / name).read_text(encoding="utf-8").strip()
    if not value or "\n" in value or "\r" in value:
        raise SystemExit(f"Invalid secret file: {name}")
    return value

bridge_values = {
    "BRIDGE_OIDC_CLIENT_ID": "keycloak-campus-bridge",
    "BRIDGE_OIDC_CLIENT_SECRET": read("identity-bridge-oidc-client-secret"),
    "OIDC_COOKIE_KEYS": ",".join(
        [
            read("identity-bridge-cookie-key-current"),
            read("identity-bridge-cookie-key-previous"),
        ]
    ),
    "OIDC_JWKS_JSON": (secret_dir / "identity-bridge-jwks.json").read_text(encoding="utf-8").strip(),
    "SMART_SHOP_API_TOKEN": read("smart-shop-login-api-token"),
    "EASY_SWU_SYNC_TOKEN": read("easy-swu-identity-sync-token"),
}
keycloak_values = {
    "KC_BOOTSTRAP_ADMIN_USERNAME": read("keycloak-bootstrap-admin-username"),
    "KC_BOOTSTRAP_ADMIN_PASSWORD": read("keycloak-bootstrap-admin-password"),
    "BRIDGE_OIDC_CLIENT_SECRET": read("identity-bridge-oidc-client-secret"),
    "SMART_SHOP_OIDC_CLIENT_SECRET": read("smart-shop-oidc-client-secret"),
    "HEADLAMP_OIDC_CLIENT_SECRET": read("headlamp-oidc-client-secret"),
    "EASY_SWU_ADMIN_OIDC_CLIENT_SECRET": read("easy-swu-admin-oidc-client-secret"),
    "YSTEMSRX_PASSWORD": read("keycloak-ystemsrx-password"),
    "ADDITIONAL_PLATFORM_ADMIN_PASSWORD": read("keycloak-additional-platform-admin-password"),
    "ZADMIN_PLATFORM_ADMIN_PASSWORD": read("keycloak-zadmin-platform-admin-password"),
    "ZJX_PLATFORM_ADMIN_PASSWORD": read("keycloak-zjx-platform-admin-password"),
    "PLATFORM_ADMIN_USERNAME": read("keycloak-platform-admin-username"),
    "PLATFORM_ADMIN_PASSWORD": read("keycloak-platform-admin-password"),
}
headlamp_values = {
    "OIDC_CLIENT_ID": "headlamp",
    "OIDC_CLIENT_SECRET": read("headlamp-oidc-client-secret"),
    "OIDC_ISSUER_URL": "https://auth.lazycampus.com/realms/lazycampus",
    "OIDC_CALLBACK_URL": "https://headlamp.lazycampus.com/oidc-callback",
    "OIDC_SCOPES": "profile,email",
}

for path, values in (
    (bridge_path, bridge_values),
    (keycloak_path, keycloak_values),
    (headlamp_path, headlamp_values),
):
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )
PY

${KUBECTL} --namespace "${NAMESPACE}" create secret generic identity-bridge-runtime \
  --from-env-file="${bridge_env}" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null
${KUBECTL} --namespace "${NAMESPACE}" create secret generic keycloak-runtime \
  --from-env-file="${keycloak_env}" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null
${KUBECTL} --namespace headlamp-system create secret generic headlamp-oidc \
  --from-env-file="${headlamp_env}" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null

python3 - "${SECRET_DIR}/smart-shop.env" "${SECRET_DIR}" <<'PY'
import os
import pathlib
import sys
import tempfile

env_path = pathlib.Path(sys.argv[1])
secret_dir = pathlib.Path(sys.argv[2])

def read(name: str) -> str:
    return (secret_dir / name).read_text(encoding="utf-8").strip()

lines = env_path.read_text(encoding="utf-8").splitlines()
admin_username = ""
for line in lines:
    if line.startswith("ADMIN_USERNAME="):
        admin_username = line.split("=", 1)[1].strip().strip("'\"").split(",", 1)[0].strip()
        break
if not admin_username:
    raise SystemExit("ADMIN_USERNAME is missing from smart-shop.env")

updates = {
    "LOGIN_API": "http://identity-bridge.identity-system.svc.cluster.local:3000/api/v1/auth/login",
    "LOGIN_API_TOKEN": read("smart-shop-login-api-token"),
    "OIDC_ISSUER": "https://auth.lazycampus.com/realms/lazycampus",
    "OIDC_CLIENT_ID": "smart-shop",
    "OIDC_CLIENT_SECRET": read("smart-shop-oidc-client-secret"),
    "OIDC_REDIRECT_URI": "https://shop-api.lazycampus.com/auth/oidc/callback",
    "OIDC_FRONTEND_URL": "https://shop.lazycampus.com",
    "OIDC_ADMIN_ROLE": "smart-shop-admin",
    "OIDC_ADMIN_USERNAME": admin_username,
}
written = set()
output = []
for line in lines:
    if "=" in line and not line.lstrip().startswith("#"):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            if key not in written:
                output.append(f"{key}={updates[key]}")
                written.add(key)
            continue
    output.append(line)
if output and output[-1] != "":
    output.append("")
for key, value in updates.items():
    if key not in written:
        output.append(f"{key}={value}")

fd, temporary = tempfile.mkstemp(prefix="smart-shop.env.", dir=str(env_path.parent))
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(output).rstrip("\n") + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, env_path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
chmod 0600 "${SECRET_DIR}/smart-shop.env"

${KUBECTL} --namespace smart-shop create secret generic smart-shop-env \
  --from-env-file="${SECRET_DIR}/smart-shop.env" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null

echo "Identity databases and runtime secrets are ready; Smart Shop and Headlamp configuration was updated."
