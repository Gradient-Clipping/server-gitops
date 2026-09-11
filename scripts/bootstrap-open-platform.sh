#!/usr/bin/env bash
# Run from a reviewed, committed server-gitops checkout on the K3s host.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRET_DIR="${SECRET_DIR:-/etc/platform-secrets}"
export SECRET_DIR
test "$(id -u)" = 0
for name in tcr-username tcr-password tencentcloud-secret-id tencentcloud-secret-key; do
  test -s "${SECRET_DIR}/${name}"
done
for name in platform-oidc-client-secret platform-campus-service-secret platform-origin-key; do
  if [[ ! -s "${SECRET_DIR}/${name}" ]]; then
    openssl rand -hex 32 | tr -d '\n' >"${SECRET_DIR}/${name}"
  fi
  chmod 0600 "${SECRET_DIR}/${name}"
done

k3s kubectl apply -f "${ROOT}/clusters/easy-platform/apps/open-platform/namespace.yaml" >/dev/null
bash "${ROOT}/scripts/provision-mysql-database.sh" lazycampus_platform open-platform mysql-platform lazycampus_platform
k3s kubectl -n open-platform create secret generic platform-runtime \
  --from-file=OIDC_CLIENT_SECRET="${SECRET_DIR}/platform-oidc-client-secret" \
  --from-file=CAMPUS_SERVICE_SECRET="${SECRET_DIR}/platform-campus-service-secret" \
  --dry-run=client -o yaml | k3s kubectl apply -f - >/dev/null
if [[ -s "${SECRET_DIR}/platform-sender-api-key" ]]; then
  test -s "${SECRET_DIR}/platform-sender-from-email"
  chmod 0600 "${SECRET_DIR}/platform-sender-api-key" "${SECRET_DIR}/platform-sender-from-email"
  k3s kubectl -n open-platform create secret generic platform-sender \
    --from-file=SENDER_API_KEY="${SECRET_DIR}/platform-sender-api-key" \
    --from-file=SENDER_FROM_EMAIL="${SECRET_DIR}/platform-sender-from-email" \
    --from-literal=SENDER_FROM_NAME='Lazy Campus' \
    --dry-run=client -o yaml | k3s kubectl apply -f - >/dev/null
fi
k3s kubectl -n easy-swu create secret generic easy-swu-open-platform \
  --from-file=OPEN_PLATFORM_SERVICE_SECRET="${SECRET_DIR}/platform-campus-service-secret" \
  --dry-run=client -o yaml | k3s kubectl apply -f - >/dev/null
k3s kubectl -n identity-system create secret generic platform-oidc-secret \
  --from-file=OIDC_CLIENT_SECRET="${SECRET_DIR}/platform-oidc-client-secret" \
  --dry-run=client -o yaml | k3s kubectl apply -f - >/dev/null

docker_config="$(mktemp /var/tmp/platform-docker.XXXXXX)"
trap 'rm -f -- "$docker_config"' EXIT
python3 - "$docker_config" "$SECRET_DIR" <<'PY'
import base64, json, pathlib, sys
directory = pathlib.Path(sys.argv[2])
username = (directory / 'tcr-username').read_text().strip()
password = (directory / 'tcr-password').read_text().strip()
auth = base64.b64encode(f'{username}:{password}'.encode()).decode()
pathlib.Path(sys.argv[1]).write_text(json.dumps({'auths': {'ccr.ccs.tencentyun.com': {'auth': auth}}}))
PY
k3s kubectl -n open-platform create secret generic tcr-auth \
  --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson="$docker_config" \
  --dry-run=client -o yaml | k3s kubectl apply -f - >/dev/null
install -d -o 999 -g 999 -m 0750 /srv/k3s-data/open-platform/redis
if [[ "${1:-}" == "--runtime-only" ]]; then
  echo 'Runtime dependencies ready. Merge GitOps manifests, then rerun without --runtime-only.'
  exit 0
fi

install -d -m 0700 /etc/nginx/private/platform-origin-keys
python3 - "$SECRET_DIR" <<'PY'
import pathlib, re, sys
key = (pathlib.Path(sys.argv[1]) / 'platform-origin-key').read_text().strip()
if not re.fullmatch(r'[a-f0-9]{64}', key):
    raise SystemExit('Invalid origin credential format')
target = pathlib.Path('/etc/nginx/private/platform-origin-keys/current.conf')
# Exact fixed-length regex avoids nginx's small default map hash bucket.
target.write_text('~^' + key + '$ 1;\n')
target.chmod(0o600)
PY

# Update only this site's file; keep all existing sites and their security rules.
site=/etc/nginx/sites-available/open-platform
link=/etc/nginx/sites-enabled/open-platform
backup="$(mktemp /var/tmp/platform-nginx.XXXXXX)"
had_site=false
had_link=false
if [[ -f "$site" ]]; then cp -p -- "$site" "$backup"; had_site=true; fi
if [[ -L "$link" ]]; then had_link=true; fi
install -m 0644 "${ROOT}/host/nginx/open-platform" "$site"
ln -sfn "$site" "$link"
if ! nginx -t; then
  if "$had_site"; then cp -p -- "$backup" "$site"; else rm -f -- "$site"; fi
  if ! "$had_link"; then rm -f -- "$link"; fi
  rm -f -- "$backup"
  exit 1
fi
rm -f -- "$backup"
systemctl reload nginx
python3 "${ROOT}/scripts/reconcile-open-platform-edge.py" --apply
echo 'Open platform secrets, database, persistent storage and host routing are ready.'
