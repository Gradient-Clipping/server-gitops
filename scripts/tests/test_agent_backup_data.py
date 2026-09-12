import importlib.util
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("backup_agent", Path(__file__).parents[2] / "clusters/easy-platform/apps/lazycampus-agent/backup_agent.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def closed_wal(self, root):
        source_directory = root / "source"
        source_directory.mkdir()
        source = source_directory / "closed.sqlite3"
        with closing(sqlite3.connect(source)) as database:
            database.execute("PRAGMA journal_mode=WAL").fetchall()
            database.execute("CREATE TABLE synthetic(value INTEGER)")
            database.executemany("INSERT INTO synthetic VALUES(?)", [(number,) for number in range(100)])
            database.commit()
        self.assertEqual(source.read_bytes()[18:20], b"\x02\x02")
        self.assertFalse(Path(str(source) + "-wal").exists())
        return source

    def readonly_source_connection(self, source):
        connect = sqlite3.connect
        source_uri = source.as_uri() + "?mode=ro"

        def wrapped(database, *args, **kwargs):
            if database == source_uri:
                error = sqlite3.OperationalError("unable to open database file")
                error.sqlite_errorcode = sqlite3.SQLITE_CANTOPEN
                raise error
            return connect(database, *args, **kwargs)

        return wrapped

    def test_closed_wal_private_copy_is_standalone_and_source_stays_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.closed_wal(root)
            before = source.read_bytes()
            with patch.object(backup.sqlite3, "connect", side_effect=self.readonly_source_connection(source)):
                backup.copy_sqlite(source, root / "copy.sqlite3")
            with closing(sqlite3.connect(root / "copy.sqlite3")) as restored:
                self.assertEqual(restored.execute("SELECT count(*) FROM synthetic").fetchone()[0], 100)
                self.assertEqual(restored.execute("PRAGMA quick_check").fetchall(), [("ok",)])
                self.assertEqual(restored.execute("PRAGMA journal_mode").fetchall(), [("delete",)])
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(list(source.parent.iterdir()), [source])
            self.assertFalse(list(root.glob(".sqlite-readonly-*")))

    @unittest.skipIf(os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0,
                     "Requires POSIX file permissions enforced for a non-root user")
    def test_real_readonly_source_without_wal_sidecars(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.closed_wal(root)
            source.chmod(0o444)
            source.parent.chmod(0o555)
            try:
                backup.copy_sqlite(source, root / "copy.sqlite3")
                with closing(sqlite3.connect(root / "copy.sqlite3")) as restored:
                    self.assertEqual(restored.execute("SELECT count(*) FROM synthetic").fetchone()[0], 100)
                self.assertEqual(list(source.parent.iterdir()), [source])
            finally:
                source.parent.chmod(0o700)
                source.chmod(0o600)

    def test_private_copy_rejects_changed_source_or_new_sidecar(self):
        for change in ("database", "wal"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = self.closed_wal(root)
                copyfile = backup.shutil.copyfile

                def changing_copy(original, target):
                    copyfile(original, target)
                    if change == "database":
                        with source.open("ab") as stream:
                            stream.write(b"changed")
                    else:
                        Path(str(source) + "-wal").write_bytes(b"synthetic-sidecar")

                with patch.object(backup.sqlite3, "connect", side_effect=self.readonly_source_connection(source)), patch.object(backup.shutil, "copyfile", side_effect=changing_copy):
                    with self.assertRaisesRegex(RuntimeError, "source changed"):
                        backup.copy_sqlite(source, root / "copy.sqlite3")
                self.assertFalse(list(root.glob(".sqlite-readonly-*")))

    def test_existing_sidecars_never_use_private_copy_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.closed_wal(root)
            Path(str(source) + "-wal").write_bytes(b"synthetic-pending-wal")
            with patch.object(backup.sqlite3, "connect", side_effect=self.readonly_source_connection(source)), patch.object(backup.shutil, "copyfile") as copyfile:
                with self.assertRaisesRegex(RuntimeError, "sidecars are unavailable"):
                    backup.copy_sqlite(source, root / "copy.sqlite3")
                copyfile.assert_not_called()

    def test_online_backup_preserves_uncheckpointed_wal_and_excludes_disposable_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = {key: root / key for key in ("workspace", "state", "published", "astrbot")}
            for source in sources.values():
                source.mkdir()
            (sources["workspace"] / "tmp").mkdir()
            (sources["workspace"] / "tmp/ephemeral.txt").write_text("ephemeral")
            (sources["workspace"] / "projects").mkdir()
            (sources["workspace"] / "projects/app.py").write_text("print('kept')")
            database = sqlite3.connect(sources["state"] / "agent.sqlite3")
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("CREATE TABLE sessions (id INTEGER)")
            database.execute("INSERT INTO sessions VALUES (42)")
            database.commit()
            try:
                record = backup.create_backup(sources, root / "backups")
                self.assertEqual(record["sqlite_databases"], 1)
                with tarfile.open(root / "backups" / record["archive"]) as archive:
                    names = archive.getnames()
                    self.assertIn("agent/workspace/projects/app.py", names)
                    self.assertNotIn("agent/workspace/tmp/ephemeral.txt", names)
                    restored = root / "restored.sqlite3"
                    restored.write_bytes(archive.extractfile("agent/state/agent.sqlite3").read())
                with closing(sqlite3.connect(restored)) as reader:
                    self.assertEqual(reader.execute("SELECT id FROM sessions").fetchall(), [(42,)])
                    self.assertEqual(reader.execute("PRAGMA quick_check").fetchone()[0], "ok")
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
