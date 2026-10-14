"""Backup failure and path boundaries, using only disposable SQLite files."""

import sqlite3
from pathlib import Path

import pytest

from scripts.backup_runtime_database import backup_database


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_backup_cannot_create_a_database_in_its_source_sidecar(tmp_path, suffix):
    source = tmp_path / "runtime.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE saved (value TEXT)")
    destination = Path(str(source) + suffix)
    with pytest.raises(ValueError):
        backup_database(source, destination)
    assert not destination.exists()
    with sqlite3.connect(source) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_corrupt_source_removes_failed_new_backup_and_preserves_original(tmp_path):
    source, destination = tmp_path / "bad.db", tmp_path / "copy.db"
    content = b"corrupted database - private contents"
    source.write_bytes(content)
    with pytest.raises(sqlite3.DatabaseError):
        backup_database(source, destination)
    assert not destination.exists()
    assert source.read_bytes() == content


def test_existing_backup_is_never_replaced_even_when_source_is_corrupt(tmp_path):
    source, destination = tmp_path / "bad.db", tmp_path / "copy.db"
    source.write_bytes(b"corrupted")
    destination.write_bytes(b"retained backup")
    with pytest.raises(ValueError):
        backup_database(source, destination)
    assert destination.read_bytes() == b"retained backup"


def test_missing_source_creates_neither_database_nor_destination_directory(tmp_path):
    source, destination = tmp_path / "missing.db", tmp_path / "new" / "copy.db"
    with pytest.raises(ValueError):
        backup_database(source, destination)
    assert not source.exists()
    assert not destination.parent.exists()
