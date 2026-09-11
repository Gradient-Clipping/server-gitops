import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import os
import socket
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('maintenance',Path(__file__).resolve().parents[1]/'host_maintenance.py')
maintenance=importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class MaintenanceTests(unittest.TestCase):
    def test_catchup_pins_versions_and_prevents_removal(self):
        command=maintenance.security_command([{'name':'openssl','candidate':'3.0.2-0ubuntu1.29'}])
        self.assertIn('openssl=3.0.2-0ubuntu1.29',command)
        self.assertIn('--no-remove',command)
        self.assertIn('Dpkg::Options::=--force-confold',command)
        with self.assertRaises(ValueError):
            maintenance.security_command([{'name':'docker-ce','candidate':'29'}])

    @unittest.skipIf(os.name=='nt','Unix socket fixture is checked on the host-compatible Linux CI')
    def test_copy_excludes_runtime_socket(self):
        with tempfile.TemporaryDirectory() as temporary, closing(socket.socket(socket.AF_UNIX)) as sock:
            sock.bind(str(Path(temporary)/'kine.sock'))
            (Path(temporary)/'state.db').write_text('file content')
            self.assertEqual(maintenance.special_files(temporary,['kine.sock','state.db']),['kine.sock'])

    def test_hash_works_on_host_python(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'content'
            path.write_bytes(b'abc')
            self.assertEqual(maintenance.sha(path),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

    def test_rejects_paths_outside_backup_root(self):
        for value in ['/srv/k3s-data', '/srv/k3s-backups/maintenance/../mysql', '/']:
            with self.assertRaises(ValueError):
                maintenance.directory(value)

    def test_backup_keeps_committed_wal_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            source=Path(temporary)/'source.db'
            target=Path(temporary)/'restored.db'
            with closing(sqlite3.connect(source)) as live:
                live.execute('PRAGMA journal_mode=WAL')
                live.execute('CREATE TABLE test(value TEXT)')
                live.execute("INSERT INTO test VALUES ('committed')")
                live.commit()
                maintenance.sqlite_copy(source,target)
                with closing(sqlite3.connect(target)) as restored:
                    self.assertEqual(restored.execute('SELECT value FROM test').fetchone()[0],'committed')

    def test_apply_requires_matching_offsite_copy_before_commands(self):
        with patch.object(maintenance,'state',return_value={'verifiedAt':'now','archiveSha256':'abc','offsiteSha256':'wrong'}), patch.object(maintenance,'run') as run:
            with self.assertRaises(ValueError):
                maintenance.apply(Path('/unused'))
            run.assert_not_called()

    def test_infrastructure_packages_are_excluded(self):
        for name in ['docker-ce','containerd.io','mysql-server','k3s']:
            self.assertIsNotNone(maintenance.EXCLUDED_PACKAGES.match(name))
        self.assertIsNone(maintenance.EXCLUDED_PACKAGES.match('openssh-server'))
