#!/usr/bin/env bash
set -euo pipefail

umask 077

KUBECTL="${KUBECTL:-k3s kubectl}"
SECRET_DIR="${SECRET_DIR:-/etc/platform-secrets}"
REGISTRY="ccr.ccs.tencentyun.com"
NAMESPACE="easy-swu"

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

required_files=(
  "${SECRET_DIR}/tcr-username"
  "${SECRET_DIR}/tcr-password"
  "${SECRET_DIR}/easy-swu-admin-oidc-client-secret"
  "${SECRET_DIR}/easy-swu-baidu-map-ak"
  "${SECRET_DIR}/easy-swu-baidu-map-sk"
  "${SECRET_DIR}/easy-swu-tailscale-auth-key"
  "${SECRET_DIR}/easy-swu-identity-sync-token"
)

for path in "${required_files[@]}"; do
  require_file "${path}"
  chmod 0600 "${path}"
done

generate_hex_file "${SECRET_DIR}/easy-swu-jwt-secret" 32
generate_hex_file "${SECRET_DIR}/easy-swu-minio-access-key" 12
generate_hex_file "${SECRET_DIR}/easy-swu-minio-secret-key" 24

${KUBECTL} create namespace "${NAMESPACE}" --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null
${KUBECTL} label namespace "${NAMESPACE}" \
  app.kubernetes.io/part-of=easy-swu \
  platform.lazycampus.com/identity-client=true \
  pod-security.kubernetes.io/enforce=restricted \
  --overwrite >/dev/null

install -d -m 0750 -o 999 -g 999 /srv/k3s-data/easy-swu/redis
install -d -m 0750 -o 1000 -g 1000 /srv/k3s-data/easy-swu/minio
install -d -m 0700 -o 1000 -g 1000 /srv/k3s-data/easy-swu/tailscale
install -d -m 0750 -o 1000 -g 1000 /srv/k3s-backups/easy-swu-minio

docker_config="$(mktemp)"
runtime_env="$(mktemp)"
tailscale_env="$(mktemp)"
cleanup() {
  rm -f -- "${docker_config}" "${runtime_env}" "${tailscale_env}"
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

${KUBECTL} --namespace "${NAMESPACE}" create secret generic tcr-auth \
  --type=kubernetes.io/dockerconfigjson \
  --from-file=.dockerconfigjson="${docker_config}" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null

provision_script="/usr/local/sbin/provision-mysql-database"
if [[ ! -x "${provision_script}" ]]; then
  provision_script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/provision-mysql-database.sh"
fi
"${provision_script}" easy_swu "${NAMESPACE}" mysql-easy-swu easy_swu_app

python3 - "${runtime_env}" "${tailscale_env}" "${SECRET_DIR}" <<'PY'
import pathlib
import sys

runtime_path = pathlib.Path(sys.argv[1])
tailscale_path = pathlib.Path(sys.argv[2])
secret_dir = pathlib.Path(sys.argv[3])

def read(name: str) -> str:
    value = (secret_dir / name).read_text(encoding="utf-8").strip()
    if not value or "\n" in value or "\r" in value:
        raise SystemExit(f"Invalid secret file: {name}")
    return value

runtime_values = {
    "JWT_SECRET": read("easy-swu-jwt-secret"),
    "ADMIN_OIDC_CLIENT_SECRET": read("easy-swu-admin-oidc-client-secret"),
    "BAIDU_MAP_AK": read("easy-swu-baidu-map-ak"),
    "BAIDU_MAP_SK": read("easy-swu-baidu-map-sk"),
    "MINIO_ACCESS_KEY": read("easy-swu-minio-access-key"),
    "MINIO_SECRET_KEY": read("easy-swu-minio-secret-key"),
    "IDENTITY_BRIDGE_SYNC_TOKEN": read("easy-swu-identity-sync-token"),
}
tailscale_values = {
    "TS_AUTHKEY": read("easy-swu-tailscale-auth-key"),
}

for path, values in ((runtime_path, runtime_values), (tailscale_path, tailscale_values)):
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )
PY

${KUBECTL} --namespace "${NAMESPACE}" create secret generic easy-swu-runtime \
  --from-env-file="${runtime_env}" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null
${KUBECTL} --namespace "${NAMESPACE}" create secret generic easy-swu-tailscale \
  --from-env-file="${tailscale_env}" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null

echo "Easy SWU database, storage directories, registry access, and runtime secrets are ready."
