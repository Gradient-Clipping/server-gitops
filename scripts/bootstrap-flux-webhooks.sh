#!/usr/bin/env bash
# Run from a committed server-gitops checkout on the K3s host.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test "$(id -u)" = 0

secret=/etc/platform-secrets/flux-github-webhook-token
install -d -m 0700 /etc/platform-secrets
if [[ ! -s "$secret" ]]; then
    openssl rand -hex 32 | tr -d '\n' > "$secret"
fi
chmod 0600 "$secret"
k3s kubectl -n flux-system create secret generic flux-github-webhook-token \
    --from-file=token="$secret" --dry-run=client -o yaml \
    | k3s kubectl apply -f - >/dev/null

site=/etc/nginx/sites-available/flux-webhooks
link=/etc/nginx/sites-enabled/flux-webhooks
backup="/var/backups/flux-webhooks-$(date +%Y%m%dT%H%M%S%z)"
install -d -m 0700 "$backup"
had_site=false
had_link=false
if [[ -f "$site" ]]; then cp -p -- "$site" "$backup/site"; had_site=true; fi
if [[ -L "$link" ]]; then cp -P -- "$link" "$backup/link"; had_link=true; fi
install -m 0644 "${ROOT}/host/nginx/flux-webhooks" "$site"
ln -sfn "$site" "$link"
if ! nginx -t; then
    if "$had_site"; then cp -p -- "$backup/site" "$site"; else rm -f -- "$site"; fi
    if "$had_link"; then cp -P --remove-destination -- "$backup/link" "$link"; else rm -f -- "$link"; fi
    nginx -t
    exit 1
fi
systemctl reload nginx
echo "Flux webhook runtime secret and host routing reconciled. Backup: $backup"
