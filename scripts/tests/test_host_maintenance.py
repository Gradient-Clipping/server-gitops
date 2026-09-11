import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('maintenance',Path(__file__).resolve().parents[1]/'host_maintenance.py')
maintenance=importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class MaintenanceTests(unittest.TestCase):
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
