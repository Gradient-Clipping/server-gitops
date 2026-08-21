#!/usr/bin/env bash
set -euo pipefail

registry="ccr.ccs.tencentyun.com"
target_repository="${registry}/lazycampus/flux-controllers"
username_file="/etc/platform-secrets/tcr-username"
password_file="/etc/platform-secrets/tcr-password"
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

controllers=(
  "helm-controller:v1.6.3"
  "image-automation-controller:v1.2.4"
  "image-reflector-controller:v1.2.4"
  "kustomize-controller:v1.9.4"
  "notification-controller:v1.9.3"
  "source-controller:v1.9.4"
)

temporary_directory="$(mktemp -d /var/tmp/flux-mirror.XXXXXX)"
case "${temporary_directory}" in
  /var/tmp/flux-mirror.*) ;;
  *)
    echo "Unexpected temporary directory: ${temporary_directory}" >&2
    exit 1
    ;;
esac

cleanup() {
  case "${temporary_directory}" in
    /var/tmp/flux-mirror.*) rm -rf -- "${temporary_directory}" ;;
  esac
}
trap cleanup EXIT

export DOCKER_CONFIG="${temporary_directory}/docker-config"
mkdir --mode=0700 "${DOCKER_CONFIG}"

for required_file in "${username_file}" "${password_file}"; do
  if [[ ! -r "${required_file}" ]]; then
    echo "Missing readable credential file: ${required_file}" >&2
    exit 1
  fi
done

docker login "${registry}" \
  --username "$(<"${username_file}")" \
  --password-stdin <"${password_file}"

for controller in "${controllers[@]}"; do
  name="${controller%%:*}"
  version="${controller#*:}"
  source_image="ghcr.io/fluxcd/${name}:${version}"
  target_image="${target_repository}:${name}-${version}"
  archive="${temporary_directory}/${name}.tar"

  if ! docker manifest inspect "${target_image}" >/dev/null 2>&1; then
    k3s ctr images export --platform linux/amd64 "${archive}" "${source_image}"
    docker load --input "${archive}" >/dev/null
    docker tag "${source_image}" "${target_image}"
    docker push "${target_image}"
    rm -f -- "${archive}"
  fi

  kubectl --namespace flux-system set image \
    "deployment/${name}" "manager=${target_image}"
done

for controller in "${controllers[@]}"; do
  name="${controller%%:*}"
  kubectl --namespace flux-system rollout status \
    "deployment/${name}" --timeout=180s
done
