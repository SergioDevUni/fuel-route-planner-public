"""The response target selects verified work without cancelling feasibility recovery."""

from concurrent.futures import TimeoutError as FutureTimeout
from decimal import Decimal
from threading import Event
from time import monotonic

import pytest

from app.api.deadline import run_with_deadline
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget


def test_preferred_return_stops_late_publication_and_keeps_snapshot():
    budget = RequestBudget()
    finished = Event()
    payload = {"status": "verified"}

    def work():
        try:
            budget.publish_verified_payload(payload, (Decimal(10), Decimal(20), 0, ()))
            while not budget.cancelled:
                finished.wait(0.005)
            with pytest.raises(PlanningDeadlineExceeded):
                budget.publish_verified_payload(
                    payload, (Decimal(9), Decimal(20), 0, ())
                )
        finally:
            finished.set()

    def ready():
        result = budget.verified_payload()
        if result is not None:
            budget.stop_work()
        return result

    began = monotonic()
    result = run_with_deadline(work, 1, preferred_seconds=0.04, on_preferred=ready)
    assert result == payload
    assert monotonic() - began < 0.5
    assert finished.wait(1)


def test_no_snapshot_at_target_continues_recovery_until_verified():
    ready, finished = Event(), Event()
    checks = []

    def recovery():
        try:
            assert ready.wait(1)
            return "verified after target"
        finally:
            finished.set()

    def preferred():
        checks.append(True)
        ready.set()
        return None

    assert (
        run_with_deadline(recovery, 1, preferred_seconds=0.02, on_preferred=preferred)
        == "verified after target"
    )
    assert checks and finished.wait(1)


def test_recovery_keeps_original_hard_deadline():
    release, finished = Event(), Event()

    def work():
        try:
            release.wait(1)
        finally:
            finished.set()

    began = monotonic()
    try:
        with pytest.raises(PlanningDeadlineExceeded):
            run_with_deadline(
                work, 0.08, preferred_seconds=0.03, on_preferred=lambda: None
            )
        assert monotonic() - began < 0.3
    finally:
        release.set()
        assert finished.wait(1)


def test_worker_failure_is_not_replaced_with_a_preferred_snapshot():
    for error in (RuntimeError("worker failed"), FutureTimeout("worker timeout")):

        def work(error=error):
            raise error

        with pytest.raises(type(error), match=str(error)):
            run_with_deadline(
                work, 1, preferred_seconds=0.03, on_preferred=lambda: "stale success"
            )
