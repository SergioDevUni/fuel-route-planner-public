"""A selected verified snapshot serves repeats even while its worker drains."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import pytest

from app.api.cache import ResultCache
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget


def test_selected_snapshot_survives_cancelled_owner_and_is_copied():
    cache = ResultCache()
    budget = RequestBudget()
    started, release = Event(), Event()

    def compute():
        started.set()
        assert release.wait(2)
        return {"cost": "late unselected result"}

    with ThreadPoolExecutor() as pool:
        owner = pool.submit(cache.get_or_compute, "key", compute, budget)
        assert started.wait(1)
        payload = {"cost": "verified"}
        budget.set_completed_payload_sink(
            lambda result: cache.put_completed("key", result)
        )
        budget.stop_work()
        budget.cache_completed_payload(payload)
        payload["cost"] = "mutated caller"
        assert cache.get_or_compute("key", lambda: pytest.fail("recalculated")) == {
            "cost": "verified"
        }
        release.set()
        with pytest.raises(PlanningDeadlineExceeded):
            owner.result(timeout=1)
    assert cache.get_or_compute("key", lambda: pytest.fail("late overwrite")) == {
        "cost": "verified"
    }


def test_existing_waiter_uses_completed_snapshot_before_owner_releases():
    cache = ResultCache()
    started, release = Event(), Event()

    def compute():
        started.set()
        assert release.wait(2)
        return {"verified": True}

    with ThreadPoolExecutor() as pool:
        owner = pool.submit(cache.get_or_compute, "key", compute, RequestBudget())
        assert started.wait(1)
        waiter = pool.submit(
            cache.get_or_compute,
            "key",
            lambda: pytest.fail("duplicate owner"),
            RequestBudget(),
        )
        cache.put_completed("key", {"verified": True})
        try:
            assert waiter.result(timeout=0.5) == {"verified": True}
            assert not owner.done()
        finally:
            release.set()
        assert owner.result(timeout=1) == {"verified": True}
    assert not cache._locks


def test_completed_snapshot_respects_key_ttl_and_capacity():
    cache = ResultCache(max_entries=1)
    cache.put_completed("old", {"cost": 1})
    cache.put_completed("new", {"cost": 2})
    assert set(cache._entries) == {"new"}
    assert cache.get_or_compute("other", lambda: {"cost": 3}) == {"cost": 3}
    expired = ResultCache(ttl_seconds=0)
    expired.put_completed("key", {"cost": 1})
    assert expired.get_or_compute("key", lambda: {"cost": 2}) == {"cost": 2}


@pytest.mark.parametrize("operation", ["get", "put"])
def test_slow_payload_copy_does_not_block_replacement_or_other_keys(
    monkeypatch, operation
):
    cache = ResultCache()
    started, release = Event(), Event()
    cache.put_completed("key", {"nested": ["old"], "slow": True})

    def gated_copy(payload):
        if payload.get("slow"):
            started.set()
            assert release.wait(2)
        return deepcopy(payload)

    monkeypatch.setattr("app.api.cache.deepcopy", gated_copy)
    with ThreadPoolExecutor(max_workers=1) as pool:
        if operation == "get":
            pending = pool.submit(cache.get_completed, "key")
        else:
            pending = pool.submit(cache.put_completed, "slow-key", {"slow": True})
        assert started.wait(1)
        try:
            # A blocked copy must not hold the global cache guard.
            acquired = cache._guard.acquire(timeout=0.1)
            if acquired:
                cache._guard.release()
            assert acquired
            cache.put_completed("key", {"nested": ["replacement"]})
            cache.put_completed("other", {"nested": ["independent"]})
            assert cache.get_completed("other") == {"nested": ["independent"]}
        finally:
            release.set()
        snapshot = pending.result(timeout=1)
    if operation == "get":
        assert snapshot == {"nested": ["old"], "slow": True}
        snapshot["nested"].append("mutated")
    assert cache.get_completed("key") == {"nested": ["replacement"]}
