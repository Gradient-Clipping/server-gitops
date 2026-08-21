#!/usr/bin/env bash
set -euo pipefail

umask 077

KUBECTL="${KUBECTL:-k3s kubectl}"
SECRET_DIR="${SECRET_DIR:-/etc/platform-secrets}"
REGISTRY="ccr.ccs.tencentyun.com"

required_files=(
  "${SECRET_DIR}/tcr-username"
  "${SECRET_DIR}/tcr-password"
  "${SECRET_DIR}/bbbto-config.json"
  "${SECRET_DIR}/bbbto-wechat-app-id"
  "${SECRET_DIR}/bbbto-wechat-app-secret"
  "${SECRET_DIR}/smart-shop.env"
  "${SECRET_DIR}/smart-shop-registration-validation.py"
)

for path in "${required_files[@]}"; do
  if [[ ! -s "${path}" ]]; then
    echo "Required secret file is missing or empty: ${path}" >&2
    exit 1
  fi
done

for namespace in ingress-system lazycampus-site bbbto-mnp smart-shop; do
  ${KUBECTL} create namespace "${namespace}" --dry-run=client -o yaml \
    | ${KUBECTL} apply -f - >/dev/null
done

docker_config="$(mktemp)"
trap 'rm -f "${docker_config}"' EXIT

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

for namespace in ingress-system lazycampus-site bbbto-mnp smart-shop; do
  ${KUBECTL} -n "${namespace}" create secret generic tcr-auth \
    --type=kubernetes.io/dockerconfigjson \
    --from-file=.dockerconfigjson="${docker_config}" \
    --dry-run=client -o yaml \
    | ${KUBECTL} apply -f - >/dev/null
done

${KUBECTL} -n bbbto-mnp create secret generic bbbto-runtime \
  --from-file=config.json="${SECRET_DIR}/bbbto-config.json" \
  --from-file=WECHAT_APP_ID="${SECRET_DIR}/bbbto-wechat-app-id" \
  --from-file=WECHAT_APP_SECRET="${SECRET_DIR}/bbbto-wechat-app-secret" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null

${KUBECTL} -n smart-shop create secret generic smart-shop-env \
  --from-env-file="${SECRET_DIR}/smart-shop.env" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null

${KUBECTL} -n smart-shop create secret generic smart-shop-registration-validation \
  --from-file=registration_validation.py="${SECRET_DIR}/smart-shop-registration-validation.py" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null

echo "Runtime secrets applied without printing secret values."
