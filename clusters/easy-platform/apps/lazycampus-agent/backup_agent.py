"""Create bounded, restorable backups with online SQLite copies; never follow symlinks."""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time

EXCLUDED = {"tmp", ".cache", "cache", "node_modules", ".venv", "__pycache__", ".pytest_cache"}


def entries(sources):
    for label, source in sources.items():
        for directory, children, files in os.walk(source, followlinks=False):
            base = Path(directory)
            relative = base.relative_to(source)
            children[:] = sorted(name for name in children if name not in EXCLUDED
                                 and not (base / name).is_symlink()
                                 and not (label == "workspace" and relative == Path(".") and name == "data"))
            for name in sorted(files):
                path = base / name
                if not path.is_file() or path.is_symlink() or name.endswith(("-wal", "-shm", "-journal", ".sock")):
                    continue
                yield label, source, path


def copy_sqlite(source, target):
    start = time.monotonic()

    def progress(status, remaining, total):
        if time.monotonic() - start > 90:
            raise TimeoutError("SQLite online backup exceeded its time budget")

    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10)) as reader:
        with closing(sqlite3.connect(target)) as writer:
            reader.backup(writer, pages=128, progress=progress, sleep=0.1)
            if writer.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("SQLite backup integrity check failed")
            writer.execute("PRAGMA journal_mode=DELETE")


def copy_file(source, target):
    with source.open("rb") as stream:
        sqlite = stream.read(16) == b"SQLite format 3\x00"
    if sqlite:
        copy_sqlite(source.resolve(), target)
        return "sqlite-online"
    for attempt in range(3):
        before = source.stat()
        shutil.copyfile(source, target, follow_symlinks=False)
        after = source.stat()
        if (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size):
            return "file"
    raise RuntimeError("A source file kept changing during backup; retry after the write completes")


def prune(output, days, now):
    pattern = re.compile(r"agent-\d{8}T\d{6}Z\.tar\.gz(?:\.json)?")
    for path in output.iterdir():
        if path.is_symlink() or path.parent.resolve() != output:
            continue
        if path.is_file() and not path.is_symlink() and pattern.fullmatch(path.name):
            if now - path.stat().st_mtime > days * 86400:
                path.unlink()
        elif now - path.stat().st_mtime > 86400:
            if path.is_dir() and re.fullmatch(r"\.agent-backup-[a-zA-Z0-9_-]+", path.name):
                shutil.rmtree(path)
            elif path.is_file() and re.fullmatch(r"agent-\d{8}T\d{6}Z\.tar\.gz\.partial", path.name):
                path.unlink()


def create_backup(sources, output, retention_days=5, max_bytes=8 * 1024**3):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    now = time.time()
    prune(output, retention_days, now)
    selected = list(entries(sources))
    total = sum(path.stat().st_size for _, _, path in selected)
    if total > max_bytes:
        raise RuntimeError("Backup source size exceeds configured budget")
    if shutil.disk_usage(output).free < total * 2 + 2 * 1024**3:
        raise RuntimeError("Insufficient free space for a complete backup")
    name = datetime.fromtimestamp(now, timezone.utc).strftime("agent-%Y%m%dT%H%M%SZ.tar.gz")
    archive_path = output / name
    if archive_path.exists():
        raise RuntimeError("Backup name already exists")
    manifest = {"created_at": datetime.fromtimestamp(now, timezone.utc).isoformat(), "format": 1,
                "sqlite_consistency": "online backup per database; not a cross-application transaction",
                "excluded": sorted(EXCLUDED | {"workspace/data", "symlinks", "sqlite-sidecars"}), "files": []}
    temporary_archive = output / (name + ".partial")
    try:
        with tempfile.TemporaryDirectory(prefix=".agent-backup-", dir=output) as temporary:
            staging = Path(temporary)
            for label, source, path in selected:
                relative = Path(label) / path.relative_to(source)
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                kind = copy_file(path, target)
                target.chmod(0o600)
                with target.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                manifest["files"].append({"path": relative.as_posix(), "size": target.stat().st_size,
                                          "sha256": digest, "kind": kind})
            (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            with tarfile.open(temporary_archive, "w:gz", compresslevel=3) as archive:
                archive.add(staging, arcname="agent", recursive=True)
        temporary_archive.chmod(0o600)
        os.replace(temporary_archive, archive_path)
        with archive_path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        summary = {"archive": name, "sha256": digest, "files": len(manifest["files"]),
                   "sqlite_databases": sum(item["kind"] == "sqlite-online" for item in manifest["files"]),
                   "bytes": archive_path.stat().st_size, "created_at": manifest["created_at"]}
        record = output / (name + ".json")
        record.write_text(json.dumps(summary, indent=2) + "\n")
        record.chmod(0o600)
        return summary
    finally:
        temporary_archive.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("/source"))
    parser.add_argument("--output", type=Path, default=Path("/backups"))
    parser.add_argument("--retention-days", type=int, default=5)
    parser.add_argument("--max-gib", type=int, default=8)
    args = parser.parse_args()
    sources = {label: args.source_root / label for label in ("workspace", "state", "published", "astrbot")}
    if any(not path.is_dir() or path.is_symlink() for path in sources.values()):
        raise ValueError("Expected all four persistent source directories")
    print(json.dumps(create_backup(sources, args.output, args.retention_days, args.max_gib * 1024**3)))


if __name__ == "__main__":
    main()
