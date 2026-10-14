from threading import BoundedSemaphore, Event
from time import monotonic

import pytest

from app.api.deadline import (
    PlanningCapacityExceeded,
    planning_capacity,
    run_with_deadline,
)
from app.planning.budget import PlanningDeadlineExceeded


def test_planning_worker_returns_a_completed_result() -> None:
    assert run_with_deadline(lambda: {"status": "verified"}, 1) == {
        "status": "verified"
    }


def test_planning_wait_times_out_while_worker_is_still_running() -> None:
    release = Event()
    finished = Event()

    def slow() -> dict:
        try:
            release.wait(1)
            return {"status": "too late"}
        finally:
            finished.set()

    began = monotonic()
    try:
        with pytest.raises(PlanningDeadlineExceeded):
            run_with_deadline(slow, 0.05)
        assert monotonic() - began < 0.25
    finally:
        release.set()
        assert finished.wait(1)


def test_saturated_planning_pool_fails_without_starting_work(monkeypatch) -> None:
    monkeypatch.setattr("app.api.deadline._planning_slots", BoundedSemaphore(0))

    def unexpected() -> dict:
        raise AssertionError("no worker should be started")

    with pytest.raises(PlanningCapacityExceeded):
        run_with_deadline(unexpected, 1)


def test_timed_out_worker_keeps_its_slot_until_completion(monkeypatch) -> None:
    monkeypatch.setattr("app.api.deadline._planning_slots", BoundedSemaphore(1))
    release, started, finished = Event(), Event(), Event()

    def work() -> None:
        started.set()
        try:
            release.wait(1)
        finally:
            finished.set()

    try:
        with pytest.raises(PlanningDeadlineExceeded):
            run_with_deadline(work, 0.05)
        assert started.is_set()
        with pytest.raises(PlanningCapacityExceeded):
            run_with_deadline(lambda: None, 1)
    finally:
        release.set()
        assert finished.wait(1)


def test_capacity_configuration_is_bounded(monkeypatch) -> None:
    monkeypatch.delenv("FUEL_PLANNING_CAPACITY", raising=False)
    assert planning_capacity() == 6
    monkeypatch.setenv("FUEL_PLANNING_CAPACITY", "12")
    assert planning_capacity() == 12
    monkeypatch.setenv("FUEL_PLANNING_CAPACITY", "0")
    with pytest.raises(ValueError):
        planning_capacity()
