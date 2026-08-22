#!/usr/bin/env bash
set -euo pipefail

umask 077

KUBECTL="${KUBECTL:-k3s kubectl}"
SECRET_DIR="${SECRET_DIR:-/etc/platform-secrets}"
MYSQL_NAMESPACE="${MYSQL_NAMESPACE:-mysql-system}"
MYSQL_HOST="mysql.mysql-system.svc.cluster.local"

usage() {
  echo "Usage: $0 <database> <application-namespace> [secret-name] [username]" >&2
  exit 2
}

[[ $# -ge 2 && $# -le 4 ]] || usage

database="$1"
application_namespace="$2"
secret_name="${3:-mysql-${database//_/-}}"
username="${4:-${database}_app}"

if [[ ! "${database}" =~ ^[a-z][a-z0-9_]{0,62}$ ]]; then
  echo "Database must match ^[a-z][a-z0-9_]{0,62}$" >&2
  exit 2
fi
if [[ ! "${username}" =~ ^[a-z][a-z0-9_]{0,31}$ ]]; then
  echo "Username must match ^[a-z][a-z0-9_]{0,31}$" >&2
  exit 2
fi
if [[ ! "${secret_name}" =~ ^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$ ]]; then
  echo "Secret name is not a valid Kubernetes DNS subdomain" >&2
  exit 2
fi
if ! ${KUBECTL} get namespace "${application_namespace}" >/dev/null 2>&1; then
  echo "Application namespace does not exist: ${application_namespace}" >&2
  exit 1
fi
if ! ${KUBECTL} --namespace "${MYSQL_NAMESPACE}" rollout status statefulset/mysql --timeout=5m >/dev/null; then
  echo "MySQL is not ready" >&2
  exit 1
fi

credential_dir="${SECRET_DIR}/mysql-apps"
password_file="${credential_dir}/${database}.password"
username_file="${credential_dir}/${database}.username"
install -d -m 0700 "${credential_dir}"

if [[ -s "${username_file}" ]]; then
  existing_username="$(<"${username_file}")"
  if [[ "${existing_username}" != "${username}" ]]; then
    echo "Database ${database} is already assigned to user ${existing_username}" >&2
    exit 1
  fi
else
  printf '%s' "${username}" >"${username_file}"
fi

if [[ ! -s "${password_file}" ]]; then
  existing_password="$(${KUBECTL} --namespace "${application_namespace}" \
    get secret "${secret_name}" -o jsonpath='{.data.MYSQL_PASSWORD}' 2>/dev/null || true)"
  if [[ -n "${existing_password}" ]]; then
    printf '%s' "${existing_password}" | base64 --decode >"${password_file}"
  else
    printf '%s' "$(openssl rand -hex 24)" >"${password_file}"
  fi
fi
chmod 0600 "${password_file}" "${username_file}"

password="$(<"${password_file}")"
if [[ ! "${password}" =~ ^[a-f0-9]{48}$ ]]; then
  echo "Stored application password for ${database} is not in the expected generated format" >&2
  exit 1
fi
printf '%s' "${password}" >"${password_file}"
sql="$(printf "CREATE DATABASE IF NOT EXISTS \`%s\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;\nCREATE USER IF NOT EXISTS '%s'@'%%' IDENTIFIED BY '%s';\nALTER USER '%s'@'%%' IDENTIFIED BY '%s';\nGRANT ALL PRIVILEGES ON \`%s\`.* TO '%s'@'%%';\nFLUSH PRIVILEGES;\n" \
  "${database}" "${username}" "${password}" "${username}" "${password}" "${database}" "${username}")"

printf '%s\n' "${sql}" \
  | ${KUBECTL} --namespace "${MYSQL_NAMESPACE}" exec -i mysql-0 --container mysql -- \
    /bin/bash -ec 'MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" mysql --protocol=socket --user=root'

secret_files="$(mktemp -d /var/tmp/mysql-provision.XXXXXX)"
case "${secret_files}" in
  /var/tmp/mysql-provision.*) ;;
  *)
    echo "Unexpected temporary directory: ${secret_files}" >&2
    exit 1
    ;;
esac
cleanup() {
  case "${secret_files}" in
    /var/tmp/mysql-provision.*) rm -rf -- "${secret_files}" ;;
  esac
}
trap cleanup EXIT
printf '%s' "${MYSQL_HOST}" >"${secret_files}/MYSQL_HOST"
printf '%s' "3306" >"${secret_files}/MYSQL_PORT"
printf '%s' "${database}" >"${secret_files}/MYSQL_DATABASE"
printf '%s' "${username}" >"${secret_files}/MYSQL_USER"
cp "${password_file}" "${secret_files}/MYSQL_PASSWORD"
printf 'mysql://%s:%s@%s:3306/%s?charset=utf8mb4' \
  "${username}" "${password}" "${MYSQL_HOST}" "${database}" >"${secret_files}/DATABASE_URL"

${KUBECTL} --namespace "${application_namespace}" create secret generic "${secret_name}" \
  --from-file="${secret_files}" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null

echo "Database ${database}, least-privilege user ${username}, and secret ${application_namespace}/${secret_name} are ready."
