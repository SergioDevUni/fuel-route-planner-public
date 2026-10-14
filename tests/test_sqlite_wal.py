"""Exercise configured SQLite durability, reader concurrency and online backup."""

import os
import sqlite3
from contextlib import closing

from scripts.backup_runtime_database import backup_database


def test_configured_wal_commit_survives_held_reader_and_online_backup(tmp_path):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.conf import settings
    from django.db.backends.sqlite3.base import DatabaseWrapper

    database = tmp_path / "runtime.sqlite3"
    configuration = {**settings.DATABASES["default"], "NAME": database}
    writer = DatabaseWrapper(configuration, alias="wal-evidence")
    try:
        writer.ensure_connection()
        with writer.cursor() as cursor:
            assert cursor.execute("PRAGMA journal_mode").fetchone() == ("wal",)
            assert cursor.execute("PRAGMA synchronous").fetchone() == (2,)
            cursor.execute("CREATE TABLE evidence (value TEXT)")
            cursor.execute("INSERT INTO evidence VALUES ('committed')")
        with closing(sqlite3.connect(database)) as reader:
            reader.execute("BEGIN")
            assert reader.execute("SELECT value FROM evidence").fetchall() == [
                ("committed",)
            ]
            # A live reader no longer blocks a durable commit from the writer.
            with writer.cursor() as cursor:
                cursor.execute("INSERT INTO evidence VALUES ('later commit')")
            assert reader.execute("SELECT COUNT(*) FROM evidence").fetchone() == (1,)
            destination = tmp_path / "backup.sqlite3"
            assert database.with_name(database.name + "-wal").stat().st_size > 0
            backup_database(database, destination)
            with closing(sqlite3.connect(destination)) as backup:
                assert backup.execute("SELECT value FROM evidence").fetchall() == [
                    ("committed",),
                    ("later commit",),
                ]
            reader.rollback()
            assert reader.execute("SELECT COUNT(*) FROM evidence").fetchone() == (2,)
    finally:
        writer.close()
