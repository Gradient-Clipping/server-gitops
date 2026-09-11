#!/usr/bin/env bash
# Idempotent restoration from a committed server-gitops checkout. Never print secrets.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRET_DIR="${SECRET_DIR:-/etc/platform-secrets}"
export SECRET_DIR
test "$(id -u)" = 0
for name in tcr-username tcr-password tencentcloud-secret-id tencentcloud-secret-key; do
  test -s "${SECRET_DIR}/${name}"
done
for name in status-admin-token status-oidc-client-secret status-origin-key; do
  if [[ ! -s "${SECRET_DIR}/${name}" ]]; then
    openssl rand -hex 32 | tr -d '\n' >"${SECRET_DIR}/${name}"
  fi
  chmod 0600 "${SECRET_DIR}/${name}"
done
k3s kubectl apply -f "${ROOT}/clusters/easy-platform/apps/status-page/namespace.yaml" >/dev/null
bash "${ROOT}/scripts/provision-mysql-database.sh" lazycampus_status status-page mysql-status lazycampus_status
k3s kubectl -n status-page create secret generic status-runtime \
  --from-file=STATUS_ADMIN_TOKEN="${SECRET_DIR}/status-admin-token" \
  --from-file=OIDC_CLIENT_SECRET="${SECRET_DIR}/status-oidc-client-secret" \
  --dry-run=client -o yaml | k3s kubectl apply -f - >/dev/null
k3s kubectl -n identity-system create secret generic status-oidc-secret \
  --from-file=OIDC_CLIENT_SECRET="${SECRET_DIR}/status-oidc-client-secret" \
  --dry-run=client -o yaml | k3s kubectl apply -f - >/dev/null
for part in api-key from-email; do
  if [[ ! -s "${SECRET_DIR}/status-sender-${part}" && -s "${SECRET_DIR}/platform-sender-${part}" ]]; then
    install -m 0600 "${SECRET_DIR}/platform-sender-${part}" "${SECRET_DIR}/status-sender-${part}"
  fi
done
if [[ -s "${SECRET_DIR}/status-sender-api-key" && -s "${SECRET_DIR}/status-sender-from-email" ]]; then
  k3s kubectl -n status-page create secret generic status-sender \
    --from-file=SENDER_API_KEY="${SECRET_DIR}/status-sender-api-key" \
    --from-file=SENDER_FROM_EMAIL="${SECRET_DIR}/status-sender-from-email" \
    --dry-run=client -o yaml | k3s kubectl apply -f - >/dev/null
fi
docker_config="$(mktemp /var/tmp/status-docker.XXXXXX)"
trap 'rm -f -- "$docker_config"' EXIT
python3 - "$docker_config" "$SECRET_DIR" <<'PY'
import base64, json, pathlib, sys
directory = pathlib.Path(sys.argv[2])
username = (directory / 'tcr-username').read_text().strip()
password = (directory / 'tcr-password').read_text().strip()
auth = base64.b64encode(f'{username}:{password}'.encode()).decode()
pathlib.Path(sys.argv[1]).write_text(json.dumps({'auths': {'ccr.ccs.tencentyun.com': {'auth': auth}}}))
PY
k3s kubectl -n status-page create secret generic tcr-auth \
  --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson="$docker_config" \
  --dry-run=client -o yaml | k3s kubectl apply -f - >/dev/null
if [[ "${1:-}" == "--runtime-only" ]]; then
  echo 'Status Page runtime credentials and database are ready.'
  exit 0
fi
install -d -m 0700 /etc/nginx/private/status-origin-keys
python3 - "$SECRET_DIR" <<'PY'
import pathlib, re, sys
key = (pathlib.Path(sys.argv[1]) / 'status-origin-key').read_text().strip()
if not re.fullmatch(r'[a-f0-9]{64}', key):
    raise SystemExit('Invalid origin credential format')
target = pathlib.Path('/etc/nginx/private/status-origin-keys/current.conf')
target.write_text('~^' + key + '$ 1;\n')
target.chmod(0o600)
PY
site=/etc/nginx/sites-available/status-page
link=/etc/nginx/sites-enabled/status-page
backup_dir="/var/backups/status-page-$(date +%Y%m%dT%H%M%S)"
install -d -m 0700 "$backup_dir"
had_site=false
had_link=false
if [[ -f "$site" ]]; then cp -p -- "$site" "$backup_dir/status-page"; had_site=true; fi
if [[ -L "$link" ]]; then had_link=true; fi
install -m 0644 "${ROOT}/host/nginx/status-page" "$site"
ln -sfn "$site" "$link"
if ! nginx -t; then
  if "$had_site"; then cp -p -- "$backup_dir/status-page" "$site"; else rm -f -- "$site"; fi
  if ! "$had_link"; then rm -f -- "$link"; fi
  exit 1
fi
systemctl reload nginx
python3 "${ROOT}/scripts/reconcile-status-page-edge.py" --apply
echo 'Status Page host routing, edge rules and runtime dependencies are ready.'
