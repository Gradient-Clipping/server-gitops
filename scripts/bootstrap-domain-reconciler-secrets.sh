#!/usr/bin/env bash
set -euo pipefail

umask 077

KUBECTL="${KUBECTL:-k3s kubectl}"
SECRET_DIR="${SECRET_DIR:-/etc/platform-secrets}"
REGISTRY="ccr.ccs.tencentyun.com"
NAMESPACE="domain-system"

required_files=(
  "${SECRET_DIR}/tcr-username"
  "${SECRET_DIR}/tcr-password"
  "${SECRET_DIR}/tencentcloud-secret-id"
  "${SECRET_DIR}/tencentcloud-secret-key"
  "${SECRET_DIR}/cloudflare-api-token"
)

for path in "${required_files[@]}"; do
  if [[ ! -s "${path}" ]]; then
    echo "Required secret file is missing or empty: ${path}" >&2
    exit 1
  fi
done

${KUBECTL} create namespace "${NAMESPACE}" --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null

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

${KUBECTL} --namespace "${NAMESPACE}" create secret generic tcr-auth \
  --type=kubernetes.io/dockerconfigjson \
  --from-file=.dockerconfigjson="${docker_config}" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null

${KUBECTL} --namespace "${NAMESPACE}" create secret generic tencentcloud-credentials \
  --from-file=secret-id="${SECRET_DIR}/tencentcloud-secret-id" \
  --from-file=secret-key="${SECRET_DIR}/tencentcloud-secret-key" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null

${KUBECTL} --namespace "${NAMESPACE}" create secret generic cloudflare-credentials \
  --from-file=api-token="${SECRET_DIR}/cloudflare-api-token" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null

echo "Domain reconciler secrets applied without printing secret values."
