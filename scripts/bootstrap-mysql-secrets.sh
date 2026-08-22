#!/usr/bin/env bash
set -euo pipefail

umask 077

KUBECTL="${KUBECTL:-k3s kubectl}"
SECRET_DIR="${SECRET_DIR:-/etc/platform-secrets}"
REGISTRY="ccr.ccs.tencentyun.com"
NAMESPACE="mysql-system"

required_files=(
  "${SECRET_DIR}/tcr-username"
  "${SECRET_DIR}/tcr-password"
  "${SECRET_DIR}/mysql-root-password"
  "${SECRET_DIR}/mysql-backup-password"
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
root_password="$(mktemp)"
backup_password="$(mktemp)"
trap 'rm -f "${docker_config}" "${root_password}" "${backup_password}"' EXIT

# Command substitution removes trailing newlines so the file contents become
# the exact MySQL password rather than a password with an invisible suffix.
printf '%s' "$(<"${SECRET_DIR}/mysql-root-password")" >"${root_password}"
printf '%s' "$(<"${SECRET_DIR}/mysql-backup-password")" >"${backup_password}"
if [[ ! -s "${root_password}" || ! -s "${backup_password}" ]]; then
  echo "MySQL password files must contain a non-whitespace value" >&2
  exit 1
fi

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

${KUBECTL} --namespace "${NAMESPACE}" create secret generic mysql-credentials \
  --from-file=MYSQL_ROOT_PASSWORD="${root_password}" \
  --from-file=MYSQL_BACKUP_PASSWORD="${backup_password}" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null

echo "MySQL registry and database credentials applied without printing secret values."
