"""API worker and caller concurrency failures."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread, current_thread

import pytest

from app.api import deadline
from app.api.rate_limit import CallerRateLimiter
from app.planning.budget import PlanningDeadlineExceeded


def test_caller_windows_remain_ordered_when_threads_are_preempted():
    entered, release, second_finished = Event(), Event(), Event()

    def clock():
        if current_thread().name == "earlier":
            entered.set()
            assert release.wait(2)
            return 100.0
        if current_thread().name == "later":
            return 102.0
        return 161.0

    limiter = CallerRateLimiter(1, 60, max_callers=2, clock=clock)
    results = []
    first = Thread(name="earlier", target=lambda: results.append(limiter.check("one")))

    def later():
        results.append(limiter.check("two"))
        second_finished.set()

    second = Thread(name="later", target=later)
    try:
        first.start()
        assert entered.wait(1)
        second.start()
        second_finished.wait(0.05)
    finally:
        release.set()
        first.join(2)
        second.join(2)
    assert results == [0, 0]
    assert limiter.check("one") == 0, (
        "Expired caller window must reset at its own expiry"
    )


def test_worker_timeout_error_is_not_assumed_to_be_planning_deadline():
    error = TimeoutError("independent worker failure")

    def work():
        raise error

    with pytest.raises(TimeoutError) as caught:
        deadline.run_with_deadline(work, 1, on_timeout=lambda: "wrong fallback")
    assert caught.value is error
    assert not isinstance(caught.value, PlanningDeadlineExceeded)


def test_executor_start_failure_releases_admission_slot(monkeypatch):
    from threading import BoundedSemaphore

    slots = BoundedSemaphore(1)
    monkeypatch.setattr(deadline, "_planning_slots", slots)
    with monkeypatch.context() as context:

        def broken(*args, **kwargs):
            raise RuntimeError("executor unavailable")

        context.setattr(deadline, "ThreadPoolExecutor", broken)
        with pytest.raises(RuntimeError):
            deadline.run_with_deadline(lambda: None, 1)
    assert deadline.run_with_deadline(lambda: "recovered", 1) == "recovered"


def test_exceptional_workers_do_not_leak_slots_or_activity():
    def work(_):
        with pytest.raises(RuntimeError):
            deadline.run_with_deadline(lambda: (_ for _ in ()).throw(RuntimeError()), 1)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(work, range(20)))
    assert deadline.planning_activity()["active_planners"] == 0
    assert deadline.run_with_deadline(lambda: "ok", 1) == "ok"
