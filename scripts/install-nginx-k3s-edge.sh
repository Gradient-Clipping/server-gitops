#!/usr/bin/env bash
set -euo pipefail

source_dir="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../host/nginx" && pwd)}"
target_dir="/etc/nginx/sites-available"
backup_dir="/var/backups/nginx-k3s-cutover-$(date +%Y%m%dT%H%M%S%z)"
configs=(lazycampus bbbto.com shop-lazycampus)

for name in "${configs[@]}"; do
  if [[ ! -s "${source_dir}/${name}" ]]; then
    echo "Missing Nginx configuration: ${source_dir}/${name}" >&2
    exit 1
  fi
  if [[ ! -f "${target_dir}/${name}" ]]; then
    echo "Existing Nginx configuration not found: ${target_dir}/${name}" >&2
    exit 1
  fi
done

install -d -m 700 "${backup_dir}"
for name in "${configs[@]}"; do
  install -m 600 "${target_dir}/${name}" "${backup_dir}/${name}"
  install -m 644 "${source_dir}/${name}" "${target_dir}/${name}"
done

if ! nginx -t; then
  echo "Nginx validation failed; restoring ${backup_dir}." >&2
  for name in "${configs[@]}"; do
    install -m 644 "${backup_dir}/${name}" "${target_dir}/${name}"
  done
  nginx -t
  exit 1
fi

systemctl reload nginx
echo "Nginx now forwards managed domains to K3s. Backup: ${backup_dir}"
