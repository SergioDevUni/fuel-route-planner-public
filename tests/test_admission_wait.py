"""Brief bursts can wait; deadlines, shutdown and real worker limits still apply."""

from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event
from time import monotonic, sleep

import pytest

from app.api import deadline
from app.planning.budget import PlanningDeadlineExceeded
from app.planning.settings import PlannerSettings


def test_burst_follower_uses_released_slot_and_records_wait(monkeypatch):
    monkeypatch.setattr(deadline, "_planning_slots", BoundedSemaphore(1))
    started, release = Event(), Event()
    waits = []

    def first():
        started.set()
        release.wait(1)
        return 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(deadline.run_with_deadline, first, 1)
        assert started.wait(1)
        follower = pool.submit(
            deadline.run_with_deadline,
            lambda: 2,
            1,
            admission_wait_seconds=0.5,
            on_admission=waits.append,
        )
        sleep(0.05)
        assert not follower.done()
        release.set()
        assert owner.result() == 1
        assert follower.result() == 2
    assert waits[0] >= 0.04
    assert deadline.planning_activity()["waiting_planners"] == 0


def test_slot_release_never_exposes_a_completed_worker_in_active_registry(monkeypatch):
    observed, released = [], Event()

    class ObservedSemaphore(BoundedSemaphore):
        def release(self, n=1):
            with deadline._activity:
                observed.append(sum(future.done() for future in deadline._workers))
            super().release(n)
            released.set()

    monkeypatch.setattr(deadline, "_planning_slots", ObservedSemaphore(1))
    assert deadline.run_with_deadline(lambda: "completed", 1) == "completed"
    assert released.wait(1)
    assert observed == [0]


def test_admission_wait_cannot_extend_request_deadline(monkeypatch):
    monkeypatch.setattr(deadline, "_planning_slots", BoundedSemaphore(0))
    began = monotonic()
    with pytest.raises((PlanningDeadlineExceeded, deadline.PlanningCapacityExceeded)):
        deadline.run_with_deadline(lambda: None, 0.05, admission_wait_seconds=1)
    assert monotonic() - began < 0.2
    assert deadline.planning_activity()["waiting_planners"] == 0


def test_admission_observer_failure_does_not_leak_capacity(monkeypatch):
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(deadline, "_planning_slots", slots)

    def broken(seconds):
        raise RuntimeError("observer failed")

    with pytest.raises(RuntimeError, match="observer failed"):
        deadline.run_with_deadline(lambda: 1, 1, on_admission=broken)
    assert deadline.run_with_deadline(lambda: 2, 1) == 2


def test_waiting_request_does_not_start_after_shutdown(monkeypatch):
    monkeypatch.setattr(deadline, "_planning_slots", BoundedSemaphore(0))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            deadline.run_with_deadline,
            lambda: pytest.fail("late work"),
            2,
            admission_wait_seconds=1,
        )
        try:
            until = monotonic() + 1
            while (
                not deadline.planning_activity()["waiting_planners"]
                and monotonic() < until
            ):
                sleep(0.005)
            monkeypatch.setattr(deadline, "_accepting_work", False)
            with pytest.raises(deadline.PlanningCapacityExceeded):
                future.result(timeout=0.2)
        finally:
            monkeypatch.setattr(deadline, "_accepting_work", True)


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "4.1"])
def test_admission_configuration_is_bounded(value):
    with pytest.raises(ValueError, match="admission"):
        PlannerSettings.from_environment({"FUEL_ADMISSION_WAIT_SECONDS": value})
