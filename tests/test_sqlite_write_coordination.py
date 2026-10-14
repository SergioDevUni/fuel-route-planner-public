"""Reproduce concurrent read/update audit writes using the configured mode."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import sleep

from config.settings import DATABASES


def test_short_immediate_transactions_serialize_read_then_write(tmp_path):
    path = tmp_path / "serving.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE review (id INTEGER PRIMARY KEY, count INTEGER)")
        db.execute("INSERT INTO review VALUES (1, 0)")
    mode = DATABASES["default"]["OPTIONS"]["transaction_mode"]
    ready = Barrier(3)

    def increment(_):
        # This verifies serialized increments, not the response time policy.
        # Hosted Windows runners can pause a transaction owner for over a second;
        # the real HTTP deadline/lock checks separately enforce the short timeout.
        with sqlite3.connect(path, timeout=5, isolation_level=None) as db:
            ready.wait(timeout=5)
            db.execute(f"BEGIN {mode}")
            old = db.execute("SELECT count FROM review WHERE id = 1").fetchone()[0]
            sleep(0.02)  # Force overlapping read/update attempts.
            db.execute("UPDATE review SET count = ? WHERE id = 1", (old + 1,))
            db.commit()

    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(increment, range(3)))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count FROM review WHERE id = 1").fetchone()[0] == 3
