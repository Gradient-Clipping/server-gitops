#!/usr/bin/env bash
set -euo pipefail

site=/etc/nginx/sites-available/bbbto.com
link=/etc/nginx/sites-enabled/bbbto.com

if [[ -e "${link}" && ! -L "${link}" ]]; then
  echo "Refusing to remove an unmanaged Nginx entry: ${link}" >&2
  exit 1
fi
if [[ -L "${link}" && "$(readlink "${link}")" != "${site}" ]]; then
  echo "Refusing to remove an unmanaged Nginx link: ${link}" >&2
  exit 1
fi
if [[ -L "${site}" ]]; then
  echo "Refusing to remove a linked Nginx site: ${site}" >&2
  exit 1
fi
if [[ -L "${link}" && ! -f "${site}" ]]; then
  echo "Refusing to remove a link without its managed Nginx site: ${link}" >&2
  exit 1
fi
if [[ -f "${site}" ]] && ! grep -Fq 'server_name bbbto.com www.bbbto.com;' "${site}"; then
  echo "Refusing to remove an unexpected Nginx site: ${site}" >&2
  exit 1
fi
if [[ ! -e "${link}" && ! -L "${link}" && ! -e "${site}" ]]; then
  echo "BBBTO Nginx site is already retired."
  exit 0
fi

backup_dir="/var/backups/bbbto-domain-retirement-$(date +%Y%m%dT%H%M%S%z)"
link_present=false
if [[ -L "${link}" ]]; then
  link_present=true
fi
install -d -m 700 "${backup_dir}"
if [[ -f "${site}" ]]; then
  install -m 600 "${site}" "${backup_dir}/bbbto.com"
fi

restore() {
  if [[ -f "${backup_dir}/bbbto.com" ]]; then
    install -m 644 "${backup_dir}/bbbto.com" "${site}"
    if [[ "${link_present}" == true ]]; then
      ln -sfn "${site}" "${link}"
    fi
  fi
}

rm -f -- "${link}" "${site}"
if ! nginx -t; then
  restore
  nginx -t
  echo "Nginx validation failed; restored the BBBTO site." >&2
  exit 1
fi
if ! systemctl reload nginx; then
  restore
  nginx -t
  systemctl reload nginx
  echo "Nginx reload failed; restored the BBBTO site." >&2
  exit 1
fi

echo "BBBTO Nginx site retired. Backup: ${backup_dir}"
