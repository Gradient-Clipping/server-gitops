#!/usr/bin/env bash
# Restore shared WeCom callback dependencies from a committed GitOps checkout.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRET_DIR="${SECRET_DIR:-/etc/platform-secrets}"
export SECRET_DIR
test "$(id -u)" = 0
for name in tcr-username tcr-password tencentcloud-secret-id tencentcloud-secret-key \
  educoder-wecom-corp-id educoder-wecom-callback-token educoder-wecom-aes-key; do
  test -s "${SECRET_DIR}/${name}"
done
key_file="${SECRET_DIR}/educoder-wecom-origin-key"
if [[ ! -s "$key_file" ]]; then
  openssl rand -hex 32 | tr -d '\n' >"$key_file"
fi
chmod 0600 "$key_file"
k3s kubectl apply -f "${ROOT}/clusters/easy-platform/apps/educoder-wecom/namespace.yaml" >/dev/null
bash "${ROOT}/scripts/provision-mysql-database.sh" educoder_wecom educoder-wecom mysql-educoder-wecom educoder_wecom
k3s kubectl -n educoder-wecom create secret generic educoder-wecom-runtime \
  --from-file=WECOM_CORP_ID="${SECRET_DIR}/educoder-wecom-corp-id" \
  --from-file=WECOM_CALLBACK_TOKEN="${SECRET_DIR}/educoder-wecom-callback-token" \
  --from-file=WECOM_ENCODING_AES_KEY="${SECRET_DIR}/educoder-wecom-aes-key" \
  --dry-run=client -o yaml | k3s kubectl apply -f - >/dev/null
docker_config="$(mktemp /var/tmp/educoder-docker.XXXXXX)"
trap 'rm -f -- "$docker_config"' EXIT
python3 - "$docker_config" "$SECRET_DIR" <<'PY'
import base64, json, pathlib, sys
directory = pathlib.Path(sys.argv[2])
username = (directory / 'tcr-username').read_text().strip()
password = (directory / 'tcr-password').read_text().strip()
auth = base64.b64encode(f'{username}:{password}'.encode()).decode()
pathlib.Path(sys.argv[1]).write_text(json.dumps({'auths': {'ccr.ccs.tencentyun.com': {'auth': auth}}}))
PY
k3s kubectl -n educoder-wecom create secret generic tcr-auth \
  --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson="$docker_config" \
  --dry-run=client -o yaml | k3s kubectl apply -f - >/dev/null
if [[ "${1:-}" == "--runtime-only" ]]; then
  echo 'WeCom KF runtime credentials and database are ready.'
  exit 0
fi
install -d -m 0700 /etc/nginx/private/educoder-origin-keys
python3 - "$SECRET_DIR" <<'PY'
import pathlib, re, sys
key = (pathlib.Path(sys.argv[1]) / 'educoder-wecom-origin-key').read_text().strip()
if not re.fullmatch(r'[a-f0-9]{64}', key):
    raise SystemExit('Invalid origin credential format')
target = pathlib.Path('/etc/nginx/private/educoder-origin-keys/current.conf')
target.write_text('~^' + key + '$ 1;\n')
target.chmod(0o600)
PY
site=/etc/nginx/sites-available/educoder-wecom
link=/etc/nginx/sites-enabled/educoder-wecom
backup_dir="/var/backups/educoder-wecom-$(date +%Y%m%dT%H%M%S)"
install -d -m 0700 "$backup_dir"
had_site=false
had_link=false
if [[ -f "$site" ]]; then cp -p -- "$site" "$backup_dir/educoder-wecom"; had_site=true; fi
if [[ -L "$link" ]]; then had_link=true; fi
install -m 0644 "${ROOT}/host/nginx/educoder-wecom" "$site"
ln -sfn "$site" "$link"
if ! nginx -t; then
  if "$had_site"; then cp -p -- "$backup_dir/educoder-wecom" "$site"; else rm -f -- "$site"; fi
  if ! "$had_link"; then rm -f -- "$link"; fi
  exit 1
fi
systemctl reload nginx
python3 "${ROOT}/scripts/reconcile-educoder-edge.py" --apply
echo 'WeCom KF host routing, edge rules and runtime dependencies are ready.'
