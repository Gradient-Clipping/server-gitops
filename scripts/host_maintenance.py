#!/usr/bin/env python3
"""Backed-up, separate host maintenance; run only from a validated Git revision."""
import argparse
from contextlib import closing
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = Path('/srv/k3s-backups/maintenance')
KEY = Path('/etc/platform-secrets/host-maintenance-backup-passphrase')
JOB = 'mysql-host-maintenance-preflight-20260912'
EXCLUDED_PACKAGES = re.compile(r'^(docker|containerd|k3s|rancher|mysql|keycloak)')


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout


def kube(*args):
    return json.loads(run(['k3s', 'kubectl', *args, '-o', 'json']))


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def directory(value):
    path = Path(value).resolve()
    if path.parent != BASE or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z', path.name):
        raise ValueError('Use an exact timestamp directory directly below the maintenance backup root')
    return path


def state(path):
    return json.loads((path / 'backup.json').read_text())


def save(path, value):
    (path / 'backup.json').write_text(json.dumps(value, indent=2) + '\n')


def sqlite_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro', uri=True)) as original, closing(sqlite3.connect(target)) as copy:
        original.backup(copy)
        if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite backup failed integrity_check')


def packages():
    import apt
    cache = apt.Cache()
    eligible = []
    excluded = []
    for package in cache:
        if not package.is_installed or not package.is_upgradable:
            continue
        candidate = package.candidate
        if not any(o.origin.startswith('Ubuntu') and o.archive.endswith('-security') for o in candidate.origins):
            continue
        item = {'name': package.name, 'installed': package.installed.version, 'candidate': candidate.version}
        (excluded if EXCLUDED_PACKAGES.match(package.name) else eligible).append(item)
    return {'eligible': eligible, 'excluded': excluded}


def special_files(parent, names):
    # Sockets/FIFOs/device nodes are process state, not recoverable file content.
    return [name for name in names if not any((
        (Path(parent)/name).is_file(), (Path(parent)/name).is_dir(),
        (Path(parent)/name).is_symlink()))]


def cleanup_staging(path):
    staging = path/'staging'
    if path.parent != BASE or staging.is_symlink() or staging.resolve() != path/'staging':
        raise ValueError('Staging cleanup boundary failed')
    if staging.exists():
        shutil.rmtree(staging)


def security_command(items):
    if not items or any(EXCLUDED_PACKAGES.match(item['name']) for item in items):
        raise ValueError('Only eligible security packages may be selected')
    return ['apt-get','--assume-yes','--no-remove','--only-upgrade',
            '-o','Dpkg::Options::=--force-confdef','-o','Dpkg::Options::=--force-confold',
            'install',*[item['name']+'='+item['candidate'] for item in items]]


def backup(path):
    job = kube('-n', 'mysql-system', 'get', 'job', JOB)
    if job.get('status', {}).get('succeeded') != 1:
        raise ValueError('The isolated MySQL restore verification job must succeed first')
    completed = datetime.datetime.fromisoformat(job['status']['completionTime'].replace('Z', '+00:00'))
    if (datetime.datetime.now(datetime.timezone.utc) - completed).total_seconds() > 14400:
        raise ValueError('MySQL restore verification is older than four hours')
    logs = run(['k3s','kubectl','-n','mysql-system','logs','job/'+JOB])
    match = re.search(r'^([0-9a-f]{64})  /backups/(all-databases-[0-9TZ]+\.sql)$', logs, re.M)
    if not match or 'full backup restore, and table checks passed' not in logs:
        raise ValueError('Restore verification proof is missing')
    dump = Path('/srv/k3s-backups/mysql') / match[2]
    if sha(dump) != match[1]:
        raise ValueError('Verified MySQL backup hash changed')
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    if not KEY.exists():
        KEY.write_text(secrets.token_urlsafe(48))
        KEY.chmod(0o600)
    staging = path / 'staging'
    staging.mkdir(mode=0o700)
    def ignore(parent, names):
        return special_files(parent,names)+[n for n in names if n.endswith(('-wal', '-shm', '.log')) or (parent == '/srv/k3s-data' and n == 'mysql')]
    shutil.copytree('/srv/k3s-data', staging/'srv/k3s-data', symlinks=True, ignore=ignore)
    sqlite_paths = []
    for source in Path('/srv/k3s-data').rglob('*'):
        if source.is_file() and source.suffix in {'.db', '.sqlite', '.sqlite3'}:
            target = staging / source.relative_to('/')
            target.unlink(missing_ok=True)
            sqlite_copy(source, target)
            sqlite_paths.append(target.relative_to(staging).as_posix())
    shutil.copytree('/var/lib/rancher/k3s/server', staging/'var/lib/rancher/k3s/server',
                    symlinks=True, ignore=lambda parent,names: special_files(parent,names)+(['db'] if parent == '/var/lib/rancher/k3s/server' else []))
    database = Path('/var/lib/rancher/k3s/server/db/state.db')
    sqlite_copy(database, staging / database.relative_to('/'))
    sqlite_paths.append(database.relative_to('/').as_posix())
    for source in ['/etc/rancher','/etc/platform-secrets','/etc/nginx','/etc/letsencrypt','/etc/apt/apt.conf.d','/etc/systemd/system','/etc/ssh','/etc/default','/etc/netplan']:
        if Path(source).exists():
            shutil.copytree(source, staging/Path(source).relative_to('/'), symlinks=True,ignore=special_files)
    for source in ['/etc/fstab','/etc/hosts','/etc/hostname','/boot/grub/grub.cfg','/var/lib/dpkg/status']:
        if Path(source).is_file():
            target=staging/Path(source).relative_to('/')
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
    (staging / KEY.relative_to('/')).unlink(missing_ok=True)
    shutil.copy2(dump, staging / 'mysql-verified.sql')
    (staging/'packages.tsv').write_text(run(['dpkg-query','-W','-f=${Package}\t${Version}\n']))
    hashes = {p.relative_to(staging).as_posix(): sha(p) for p in staging.rglob('*') if p.is_file() and not p.is_symlink()}
    archive = path / 'host-recovery.tar.gz.gpg'
    with open(path/'encryption.log','w') as log:
        process = subprocess.Popen(['gpg','--batch','--yes','--pinentry-mode','loopback','--passphrase-file',str(KEY),
                                    '--symmetric','--cipher-algo','AES256','--output',str(archive)], stdin=subprocess.PIPE, stdout=log, stderr=log)
        try:
            with tarfile.open(fileobj=process.stdin,mode='w|gz') as tar:
                tar.add(staging, arcname='recovery')
        finally:
            process.stdin.close()
        if process.wait() != 0:
            raise ValueError('Backup encryption failed; inspect root-only diagnostics')
    save(path, {'createdAt':now(),'archiveSha256':sha(archive),'files':hashes,'sqlite':sqlite_paths,
                'mysqlRestoreJob':JOB,'mysqlDumpSha256':match[1],'kernelBefore':run(['uname','-r']).strip()})
    verify(path)
    cleanup_staging(path)
    print(json.dumps({'backup':str(path),'sha256':sha(archive),'restoredFiles':len(hashes),'sqliteChecks':len(sqlite_paths)}))


def verify(path):
    record = state(path)
    archive = path / 'host-recovery.tar.gz.gpg'
    if sha(archive) != record['archiveSha256']:
        raise ValueError('Encrypted archive hash mismatch')
    found = set()
    with tempfile.TemporaryDirectory(prefix='verify-',dir=path) as temporary, open(path/'decryption.log','w') as log:
        process = subprocess.Popen(['gpg','--batch','--pinentry-mode','loopback','--passphrase-file',str(KEY),
                                    '--decrypt',str(archive)], stdout=subprocess.PIPE, stderr=log)
        try:
            with tarfile.open(fileobj=process.stdout, mode='r|gz') as tar:
                for member in tar:
                    if not member.isfile():
                        continue
                    relative = member.name.removeprefix('recovery/')
                    if relative not in record['files'] or relative in found:
                        raise ValueError('Unexpected backup member')
                    stream = tar.extractfile(member)
                    digest = hashlib.sha256()
                    target = Path(temporary) / ('sqlite-' + str(len(found)))
                    with open(target, 'wb') as restored:
                        while block := stream.read(1024 * 1024):
                            digest.update(block)
                            if relative in record['sqlite']:
                                restored.write(block)
                    if digest.hexdigest() != record['files'][relative]:
                        raise ValueError('Restored file content mismatch')
                    if relative in record['sqlite']:
                        with closing(sqlite3.connect(target)) as db:
                            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                                raise ValueError('Restored SQLite failed integrity_check')
                    found.add(relative)
        finally:
            process.stdout.close()
        if process.wait() or found != set(record['files']):
            raise ValueError('Decryption or archive completeness check failed')
    record['verifiedAt'] = now()
    save(path, record)


def apply(path):
    record = state(path)
    if not record.get('verifiedAt') or record.get('offsiteSha256') != record['archiveSha256']:
        raise ValueError('Verified backup and matching encrypted offsite copy are required')
    if record.get('packagesAppliedAt'):
        raise ValueError('Packages already applied; continue with reboot or postcheck')
    verify(path)
    run(['nginx','-t'])
    if run(['dpkg','--audit']).strip():
        raise ValueError('Resolve the package database audit before upgrading')
    # Timers resume even after failure; no package removal or infrastructure
    # version migration is requested by this maintenance operation.
    run(['systemctl','stop','apt-daily.timer','apt-daily-upgrade.timer'])
    try:
        target = Path('/etc/apt/apt.conf.d/99-platform-security')
        shutil.copy2(ROOT/'host/apt/99-platform-security', target)
        target.chmod(0o644)
        with open(path/'security-upgrade.log','a') as log:
            environment={**os.environ,'DEBIAN_FRONTEND':'noninteractive','NEEDRESTART_MODE':'l'}
            result=subprocess.run(['apt-get','update'],stdout=log,stderr=log,env=environment,timeout=600)
            if result.returncode:
                raise ValueError('APT refresh failed; inspect the maintenance log')
            eligible=packages()['eligible']
            (path/'selected-security.json').write_text(json.dumps(eligible,indent=2))
            if eligible:
                command=security_command(eligible)
                simulation=run(command[:1]+['--simulate']+command[1:])
                planned=re.findall(r'^Inst (\S+)',simulation,re.M)
                if any(EXCLUDED_PACKAGES.match(name) for name in planned) or re.search(r'^Remv ',simulation,re.M):
                    raise ValueError('APT simulation changed excluded infrastructure or removed a package')
                log.write(simulation)
                log.flush()
                result = subprocess.run(command,stdout=log,stderr=log,env={**os.environ,'DEBIAN_FRONTEND':'noninteractive','NEEDRESTART_MODE':'l'},timeout=2400)
                if result.returncode:
                    raise ValueError('Security update failed; inspect the root-only maintenance log')
        if run(['dpkg','--audit']).strip():
            raise ValueError('Post-upgrade dpkg audit failed')
        run(['nginx','-t'])
        remaining = packages()
        (path/'remaining-security.json').write_text(json.dumps(remaining,indent=2))
        if remaining['eligible']:
            raise ValueError('Security packages remain eligible; inspect before reboot')
        shutil.copy2(ROOT/'host/k3s/config.yaml','/etc/rancher/k3s/config.yaml')
        Path('/etc/rancher/k3s/config.yaml').chmod(0o600)
        record = state(path)
        record['packagesAppliedAt'] = now()
        record['stagedK3sSha256'] = sha(ROOT/'host/k3s/config.yaml')
        record['bootIdBefore'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        save(path,record)
    finally:
        run(['systemctl','start','apt-daily.timer','apt-daily-upgrade.timer'])
    print('Security backlog cleared; validated node reservations staged for the maintenance reboot.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['plan','backup','verify','cleanup-staging','confirm-offsite','apply','reboot','postcheck'])
    parser.add_argument('--directory',required=True,type=directory)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('Host maintenance requires root')
    os.umask(0o077)
    path = args.directory
    if args.phase == 'plan':
        print(json.dumps(packages(),indent=2))
    elif args.phase == 'backup':
        backup(path)
    elif args.phase == 'verify':
        verify(path)
        print('Encrypted backup, all files and restored SQLite databases verified.')
    elif args.phase == 'cleanup-staging':
        cleanup_staging(path)
        print('Only the named maintenance staging directory was removed.')
    elif args.phase == 'confirm-offsite':
        record = state(path)
        if args.sha256 != record['archiveSha256']:
            raise ValueError('Offsite hash does not match')
        record['offsiteSha256'] = args.sha256
        record['offsiteConfirmedAt'] = now()
        save(path,record)
        print('Matching encrypted offsite copy recorded.')
    elif args.phase == 'apply':
        apply(path)
    elif args.phase == 'reboot':
        record = state(path)
        if not record.get('packagesAppliedAt') or sha('/etc/rancher/k3s/config.yaml') != record['stagedK3sSha256']:
            raise ValueError('Complete package updates and validated node configuration first')
        if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != record['bootIdBefore']:
            raise ValueError('Host has already rebooted; perform postcheck')
        run(['systemd-run','--unit=platform-maintenance-reboot','--on-active=10s','/usr/bin/systemctl','reboot'])
        print('Maintenance reboot scheduled in 10 seconds.')
    else:
        record = state(path)
        checks = {'kernel':run(['uname','-r']).strip(),'bootChanged':Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=record.get('bootIdBefore'),
                  'dpkgAudit':run(['dpkg','--audit']).strip(),'eligibleSecurityCount':len(packages()['eligible']),
                  'node':kube('get','node','easy-platform-1')['status'],
                  'kubelet':json.loads(run(['k3s','kubectl','get','--raw','/api/v1/nodes/easy-platform-1/proxy/configz']))['kubeletconfig']}
        # Only resource configuration is reported; no credentials or full config.
        checks['kubelet']={k:checks['kubelet'].get(k) for k in ['kubeReserved','systemReserved','evictionHard']}
        (path/'postcheck.json').write_text(json.dumps(checks,indent=2))
        print(json.dumps(checks,indent=2))


if __name__ == '__main__':
    main()
