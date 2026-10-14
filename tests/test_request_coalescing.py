from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event
from time import sleep

import pytest

from app.api.coalescing import RequestCoalescer
from app.api.deadline import run_with_deadline
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget


def test_followers_share_one_admitted_worker_and_receive_independent_payloads(
    monkeypatch,
):
    monkeypatch.setattr("app.api.deadline._planning_slots", BoundedSemaphore(1))
    coalescer = RequestCoalescer()
    started, release = Event(), Event()

    def work():
        started.set()
        assert release.wait(2)
        return {"nested": {"value": 1}}

    def admitted():
        return run_with_deadline(work, 2)

    follower = RequestBudget()
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(coalescer.run, "key", admitted, RequestBudget())
        assert started.wait(1)
        other = pool.submit(coalescer.run, "key", admitted, follower)
        sleep(0.05)
        release.set()
        first, second = owner.result(), other.result()
    second["nested"]["value"] = 2
    assert first["nested"]["value"] == 1
    assert follower.metrics["coalesced_result_reuses"] == 1
    assert not coalescer._flights


def test_follower_timeout_does_not_cancel_owner():
    coalescer = RequestCoalescer()
    started, release = Event(), Event()

    def work():
        started.set()
        assert release.wait(2)
        return {"ok": True}

    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(coalescer.run, "key", work, RequestBudget())
        assert started.wait(1)
        try:
            with pytest.raises(PlanningDeadlineExceeded):
                coalescer.run("key", work, RequestBudget(deadline_seconds=0.02))
        finally:
            release.set()
        assert owner.result() == {"ok": True}


def test_failed_owner_is_removed_and_retry_can_succeed():
    coalescer = RequestCoalescer()

    def fail():
        raise ValueError("provider failed")

    with pytest.raises(ValueError, match="provider failed"):
        coalescer.run("key", fail, RequestBudget())
    assert coalescer.run("key", lambda: {"ok": True}, RequestBudget()) == {"ok": True}
