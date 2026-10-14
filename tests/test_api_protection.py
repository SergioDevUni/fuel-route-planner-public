"""Independent concurrency, quota, lifecycle and safe-log regressions."""

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread

import pytest

from app.api import deadline
from app.api.observability import OperationalFormatter
from app.api.rate_limit import CallerRateLimiter
from scripts.backup_runtime_database import backup_database


def test_quota_resets_and_does_not_trust_another_caller() -> None:
    now = [100.0]
    limiter = CallerRateLimiter(2, 60, clock=lambda: now[0])
    assert limiter.check("one") == 0
    assert limiter.check("one") == 0
    assert limiter.check("one") == 60
    assert limiter.check("two") == 0
    now[0] += 59.5
    assert limiter.check("one") == 1
    now[0] += 0.5
    assert limiter.check("one") == 0


def test_quota_memory_bound_does_not_evict_active_caller() -> None:
    now = [0.0]
    limiter = CallerRateLimiter(1, 60, 1, clock=lambda: now[0])
    assert limiter.check("one") == 0
    assert limiter.check("two") == 60
    assert limiter.check("one") == 60
    now[0] = 60
    assert limiter.check("two") == 0


def test_concurrent_requests_cannot_exceed_caller_quota() -> None:
    limiter = CallerRateLimiter(5, 60)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(limiter.check, ["caller"] * 40))
    assert results.count(0) == 5


def test_operational_log_omits_tokens_paths_and_exception_text() -> None:
    record = logging.LogRecord("api", logging.ERROR, "", 0, "api_failure", (), None)
    record.request_id = "correlation"
    record.authorization = "Bearer private-token"
    record.path = "/maps/location?secret=value"
    record.exc_info = (ValueError, ValueError("private-key"), None)
    output = OperationalFormatter().format(record)
    assert "correlation" in output
    assert "private" not in output
    assert "location" not in output


def test_shutdown_stops_admission_and_drains_active_work(monkeypatch) -> None:
    monkeypatch.setattr(deadline, "_accepting_work", True)
    began, release, finished = Event(), Event(), Event()

    def work():
        began.set()
        release.wait(2)
        finished.set()

    thread = Thread(
        target=lambda: deadline.run_with_deadline(work, 3, on_shutdown=release.set)
    )
    thread.start()
    assert began.wait(1)
    assert deadline.shutdown_planning(1)
    assert finished.is_set()
    assert deadline.planning_activity()["active_planners"] == 0
    with pytest.raises(deadline.PlanningCapacityExceeded):
        deadline.run_with_deadline(lambda: None, 1)
    thread.join(1)


def test_online_backup_restores_committed_wal_rows(tmp_path: Path) -> None:
    source, copy = tmp_path / "runtime.db", tmp_path / "backup.db"
    with sqlite3.connect(source) as database:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("CREATE TABLE results (value TEXT)")
        database.execute("INSERT INTO results VALUES ('saved')")
        database.commit()
        backup_database(source, copy)
        database.execute("INSERT INTO results VALUES ('later')")
    with sqlite3.connect(copy) as restored:
        assert restored.execute("SELECT value FROM results").fetchall() == [("saved",)]
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    with pytest.raises(ValueError):
        backup_database(source, copy)
    with pytest.raises(ValueError):
        backup_database(source, source)


def test_shutdown_timeout_reports_late_worker_and_keeps_slot(monkeypatch) -> None:
    monkeypatch.setattr(deadline, "_accepting_work", True)
    release, began = Event(), Event()

    def work():
        began.set()
        release.wait(2)

    try:
        with pytest.raises(deadline.PlanningDeadlineExceeded):
            deadline.run_with_deadline(work, 0.02)
        assert began.is_set()
        assert deadline.planning_activity()["late_planners"] == 1
        assert not deadline.shutdown_planning(timeout=0.02)
        assert deadline.planning_activity()["active_planners"] == 1
        with pytest.raises(deadline.PlanningCapacityExceeded):
            deadline.run_with_deadline(lambda: None, 1)
    finally:
        release.set()
        assert deadline.shutdown_planning(timeout=1)
