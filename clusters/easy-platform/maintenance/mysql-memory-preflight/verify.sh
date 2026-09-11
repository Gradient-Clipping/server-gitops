#!/usr/bin/env bash
set -euo pipefail
umask 077

# The production connection is read-only. Restoration uses an emptyDir, no TCP
# listener, no scheduled events, and the exact image/configuration being tested.
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
partial="/backups/all-databases-${timestamp}.sql.partial"
backup="/backups/all-databases-${timestamp}.sql"
socket=/tmp/mysql-restore.sock
restore_pid=
cleanup() {
  if [[ -n "${restore_pid}" ]]; then
    kill -TERM "${restore_pid}" 2>/dev/null || true
    wait "${restore_pid}" 2>/dev/null || true
  fi
  # Only an incomplete dump from this invocation may be removed.
  rm -f -- "${partial}"
}
trap cleanup EXIT

inventory="SELECT TABLE_SCHEMA, COUNT(*) FROM information_schema.tables
WHERE TABLE_SCHEMA NOT IN ('information_schema','performance_schema','sys')
GROUP BY TABLE_SCHEMA ORDER BY TABLE_SCHEMA"
mysql --host=mysql --user=platform_backup --batch --skip-column-names \
  --execute="${inventory}" >/tmp/source-tables.tsv
mysqldump --host=mysql --user=platform_backup --all-databases \
  --single-transaction --routines --events --triggers --hex-blob \
  --no-tablespaces --set-gtid-purged=OFF >"${partial}"
test -s "${partial}"
tail -n 1 "${partial}" | grep -q '^-- Dump completed on '
mv -- "${partial}" "${backup}"
sha256sum "${backup}"
echo 'Fresh production backup completed.'

mysqld --defaults-file=/candidate/platform.cnf --validate-config
mkdir -p /restore/data
mysqld --defaults-file=/candidate/platform.cnf --datadir=/restore/data \
  --initialize-insecure --skip-log-bin --log-error=/tmp/initialize.log
mysqld --defaults-file=/candidate/platform.cnf --datadir=/restore/data \
  --socket="${socket}" --pid-file=/tmp/mysql-restore.pid \
  --skip-networking --skip-grant-tables --skip-log-bin --event-scheduler=OFF \
  --log-error=/tmp/restore.log &
restore_pid=$!
ready=false
for ((attempt=0; attempt<120; attempt++)); do
  if MYSQL_PWD= mysqladmin --protocol=socket --socket="${socket}" \
    --user=root ping --silent >/dev/null 2>&1; then
    ready=true
    break
  fi
  kill -0 "${restore_pid}"
  sleep 1
done
[[ "${ready}" == true ]]
if ! MYSQL_PWD= mysql --protocol=socket --socket="${socket}" --user=root \
  <"${backup}" >/tmp/restore-client.log 2>&1; then
  echo 'Backup restore failed; diagnostics remain inside the verification pod.' >&2
  exit 1
fi
MYSQL_PWD= mysql --protocol=socket --socket="${socket}" --user=root \
  --batch --skip-column-names --execute="${inventory}" >/tmp/restored-tables.tsv
if [[ "$(cat /tmp/source-tables.tsv)" != "$(cat /tmp/restored-tables.tsv)" ]]; then
  echo 'Restored database/table inventory differs from the source.' >&2
  exit 1
fi
if ! MYSQL_PWD= mysqlcheck --protocol=socket --socket="${socket}" --user=root \
  --check --all-databases >/tmp/table-check.log 2>&1; then
  echo 'Restored table integrity check failed.' >&2
  exit 1
fi
echo 'Restored database/table inventory:'
cat /tmp/restored-tables.tsv
MYSQL_PWD= mysql --protocol=socket --socket="${socket}" --user=root --batch \
  --execute="SELECT SUBSTRING_INDEX(EVENT_NAME,'/',2) AS category,
    ROUND(SUM(CURRENT_NUMBER_OF_BYTES_USED)/1048576,2) AS current_MiB
    FROM performance_schema.memory_summary_global_by_event_name
    GROUP BY category HAVING SUM(CURRENT_NUMBER_OF_BYTES_USED)>1048576
    ORDER BY SUM(CURRENT_NUMBER_OF_BYTES_USED) DESC"
echo 'Candidate startup, full backup restore, and table checks passed.'
