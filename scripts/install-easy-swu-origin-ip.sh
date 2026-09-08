#!/usr/bin/env bash
set -euo pipefail
umask 077

source_file="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../host/nginx" && pwd)/easy-swu}"
target_file=/etc/nginx/sites-available/easy-swu
secret_file=/etc/platform-secrets/easy-swu-origin-key
key_dir=/etc/nginx/private/easy-swu-origin-keys
backup_dir="/var/backups/easy-swu-origin-ip-$(date +%Y%m%dT%H%M%S%N%z)"

if [[ $(id -u) -ne 0 || ! -s "$source_file" || ! -f "$target_file" ]]; then
  echo "Run as root with a valid source file and an existing Easy SWU Nginx site." >&2
  exit 1
fi

# Keep the existing key on subsequent deployments. Never print it or put it in Git.
install -d -m 700 /etc/platform-secrets /etc/nginx/private "$key_dir" "$backup_dir"
python3 - "$secret_file" "$key_dir/active.conf" <<'PY'
import os
import re
import secrets
import sys
from pathlib import Path

secret_path, map_path = map(Path, sys.argv[1:])
if not secret_path.exists():
    fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(secrets.token_hex(32) + "\n")
key = secret_path.read_text().strip()
if not re.fullmatch(r"[a-f0-9]{64}", key):
    raise SystemExit("Existing origin key must contain exactly 64 lowercase hex characters.")
secret_path.chmod(0o600)
temporary = map_path.with_suffix(".tmp")
temporary.write_text('"~^' + key + '$" 1;\n')
temporary.chmod(0o600)
temporary.replace(map_path)
PY

install -m 600 "$target_file" "$backup_dir/easy-swu"
restore_site() {
  install -m 644 "$backup_dir/easy-swu" "$target_file"
  nginx -t && systemctl reload nginx
}
trap 'restore_site' ERR
install -m 644 "$source_file" "$target_file"
nginx -t
systemctl reload nginx
trap - ERR
echo "Easy SWU origin-IP configuration installed. Backup: $backup_dir"
echo "Origin key stays in $secret_file; configure the two Easy SWU hosts in EdgeOne."
