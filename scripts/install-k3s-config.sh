#!/usr/bin/env bash
set -euo pipefail

source_file="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../host/k3s" && pwd)/config.yaml}"
target_file="/etc/rancher/k3s/config.yaml"
backup_dir="/var/backups/k3s-config-$(date +%Y%m%dT%H%M%S%z)"

if [[ ! -s "${source_file}" ]]; then
  echo "Missing K3s configuration: ${source_file}" >&2
  exit 1
fi

if cmp -s "${source_file}" "${target_file}"; then
  echo "K3s configuration is already current."
  exit 0
fi

install -d -m 0700 "${backup_dir}"
if [[ -f "${target_file}" ]]; then
  install -m 0600 "${target_file}" "${backup_dir}/config.yaml"
fi
install -m 0600 "${source_file}" "${target_file}"

if ! systemctl restart k3s; then
  if [[ -f "${backup_dir}/config.yaml" ]]; then
    install -m 0600 "${backup_dir}/config.yaml" "${target_file}"
    systemctl restart k3s
  fi
  echo "K3s restart failed; the previous configuration was restored." >&2
  exit 1
fi

for _ in $(seq 1 60); do
  if k3s kubectl get --raw=/readyz >/dev/null 2>&1; then
    echo "K3s is ready with OIDC authentication enabled. Backup: ${backup_dir}"
    exit 0
  fi
  sleep 2
done

if [[ -f "${backup_dir}/config.yaml" ]]; then
  install -m 0600 "${backup_dir}/config.yaml" "${target_file}"
  systemctl restart k3s
fi
echo "K3s did not become ready; the previous configuration was restored." >&2
exit 1
