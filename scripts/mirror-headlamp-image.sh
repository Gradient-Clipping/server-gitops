#!/usr/bin/env bash
set -euo pipefail

version="${1:-v0.44.0}"
registry="ccr.ccs.tencentyun.com"
source_image="ghcr.io/headlamp-k8s/headlamp:${version}"
target_image="${registry}/lazycampus/headlamp:${version}"
username_file="/etc/platform-secrets/tcr-username"
password_file="/etc/platform-secrets/tcr-password"

for path in "${username_file}" "${password_file}"; do
  if [[ ! -s "${path}" ]]; then
    echo "Missing readable credential file: ${path}" >&2
    exit 1
  fi
done

temporary_directory="$(mktemp -d /var/tmp/headlamp-mirror.XXXXXX)"
case "${temporary_directory}" in
  /var/tmp/headlamp-mirror.*) ;;
  *)
    echo "Unexpected temporary directory: ${temporary_directory}" >&2
    exit 1
    ;;
esac

cleanup() {
  case "${temporary_directory}" in
    /var/tmp/headlamp-mirror.*) rm -rf -- "${temporary_directory}" ;;
  esac
}
trap cleanup EXIT

export DOCKER_CONFIG="${temporary_directory}/docker-config"
mkdir --mode=0700 "${DOCKER_CONFIG}"
docker login "${registry}" \
  --username "$(<"${username_file}")" \
  --password-stdin <"${password_file}" >/dev/null

docker pull --platform linux/amd64 "${source_image}"
docker tag "${source_image}" "${target_image}"
docker push "${target_image}"
docker image inspect "${target_image}" --format '{{index .RepoDigests 0}}'
