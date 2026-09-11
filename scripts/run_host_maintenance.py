#!/usr/bin/env python3
"""Run a host maintenance phase from the exact CI-validated production tree."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

from production_gate import api, validate_run

ROOT = Path(__file__).resolve().parents[1]
SERVER = 'root@1.14.95.189'
KEY = '/etc/platform-secrets/host-maintenance-backup-passphrase'


def run(args, data=None, binary=False, timeout=180):
    result = subprocess.run(args,input=data,capture_output=True,text=not binary,
                            **({} if binary else {'encoding':'utf-8'}),timeout=timeout,cwd=ROOT)
    if result.returncode:
        # SSH stderr may contain diagnostics but never print captured key data.
        raise RuntimeError(f'{Path(args[0]).name} failed ({result.returncode}): '+str(result.stderr)[-1800:])
    return result.stdout


def ssh(command, data=None, binary=False, timeout=180):
    return run([shutil.which('ssh.exe') or 'ssh','-o','BatchMode=yes','-o','ConnectTimeout=15',SERVER,command],data,binary,timeout)


def dpapi(secret, protect):
    if os.name != 'nt':
        raise ValueError('Offsite key export currently requires the operator Windows DPAPI profile')
    code = '$v=[Console]::In.ReadToEnd(); Add-Type -AssemblyName System.Security; '
    if protect:
        code += '[Convert]::ToBase64String([Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($v),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser))'
    else:
        code += '[Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($v),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser))'
    return run(['powershell.exe','-NoProfile','-Command',code],secret).strip()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['plan','backup','verify','cleanup-staging','finish-update-chunk','export','apply','reboot','postcheck'])
    parser.add_argument('--revision',required=True)
    parser.add_argument('--run-id',required=True,type=int)
    parser.add_argument('--directory',required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--pid',type=int)
    args=parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}',args.revision) or not re.fullmatch(r'/srv/k3s-backups/maintenance/[0-9]{8}T[0-9]{6}Z',args.directory):
        raise ValueError('Exact revision and maintenance timestamp directory required')
    config=json.loads((ROOT/'config/production.json').read_text())
    prefix='repos/'+config['repository']
    validation=api(f'{prefix}/actions/runs/{args.run_id}')
    jobs=api(f'{prefix}/actions/runs/{args.run_id}/jobs?filter=latest&per_page=100')['jobs']
    validate_run(config,args.revision,validation,jobs)
    if validation.get('conclusion') != 'success' or api(f'{prefix}/git/ref/heads/production')['object']['sha'] != args.revision:
        raise ValueError('Use the current successfully validated production revision')
    run(['git','fetch','origin','production'])
    archive=run(['git','archive','--format=tar',args.revision,'scripts/host_maintenance.py','host/k3s/config.yaml','host/apt/99-platform-security'],binary=True)
    remote='/var/lib/platform-gitops/'+args.revision
    ssh('install -d -m 0700 '+shlex.quote(remote)+' && tar -xf - -C '+shlex.quote(remote),archive,binary=True)
    command=['python3',remote+'/scripts/host_maintenance.py',args.phase,'--directory',args.directory]
    if args.phase=='finish-update-chunk':
        if not args.pid:
            raise ValueError('An exact maintenance process PID is required')
        command+=['--pid',str(args.pid)]
    if args.phase=='export':
        if not args.output:
            raise ValueError('Provide an offsite output directory')
        args.output.mkdir(parents=True,exist_ok=True)
        record=json.loads(ssh('cat '+shlex.quote(args.directory+'/backup.json')))
        archive_file=args.output/'host-recovery.tar.gz.gpg'
        run([shutil.which('scp.exe') or 'scp','-q',SERVER+':'+args.directory+'/host-recovery.tar.gz.gpg',str(archive_file)],timeout=900)
        with archive_file.open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest != record['archiveSha256']:
            raise ValueError('Offsite ciphertext hash mismatch')
        secret=ssh('cat '+KEY).strip()
        encrypted=dpapi(secret,True)
        if dpapi(encrypted,False) != secret:
            raise ValueError('DPAPI key recovery check failed')
        (args.output/'backup-key.dpapi').write_text(encrypted,encoding='ascii')
        secret=None
        record['offsiteSha256']=digest
        (args.output/'backup-verification.json').write_text(json.dumps({k:v for k,v in record.items() if k!='files'},indent=2),encoding='utf-8')
        command[2]='confirm-offsite'
        command += ['--sha256',digest]
    output=ssh(shlex.join(command),timeout=5400)
    if args.output and args.phase!='export':
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(output,encoding='utf-8')
        print(f'{args.phase}: result saved to {args.output}')
    else:
        print(output.strip())


if __name__=='__main__':
    main()
