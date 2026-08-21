#!/usr/bin/env bash
set -euo pipefail

config_path="${K3S_CONFIG_PATH:-/etc/rancher/k3s/config.yaml}"

if [[ ! -f "${config_path}" ]]; then
  echo "K3s configuration not found: ${config_path}" >&2
  exit 1
fi

if grep -q 'nodeport-addresses=127\.0\.0\.0/8' "${config_path}"; then
  echo "K3s NodePorts are already restricted to loopback."
  exit 0
fi

if grep -q '^kube-proxy-arg:' "${config_path}"; then
  echo "kube-proxy-arg already exists; merge nodeport-addresses manually." >&2
  exit 1
fi

backup_path="${config_path}.before-nodeport-loopback.$(date +%Y%m%dT%H%M%S%z)"
install -m 600 "${config_path}" "${backup_path}"

printf '\nkube-proxy-arg:\n  - nodeport-addresses=127.0.0.0/8\n' >>"${config_path}"

systemctl restart k3s

for _ in $(seq 1 60); do
  if k3s kubectl wait --for=condition=Ready node/easy-platform-1 --timeout=2s >/dev/null 2>&1; then
    echo "K3s restarted with loopback-only NodePorts."
    exit 0
  fi
  sleep 1
done

echo "K3s did not return to Ready; restore ${backup_path} and restart k3s." >&2
exit 1
