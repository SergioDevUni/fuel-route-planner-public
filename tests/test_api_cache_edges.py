"""Saved plan reuse must be isolated, bounded and deadline-aware."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.api.cache import ResultCache
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget


def test_nested_result_mutation_cannot_change_the_cached_plan():
    cache = ResultCache()
    source = {"costs": {"total": "10"}, "fuel_stops": [{"station_id": "csv-1"}]}
    first = cache.get_or_compute("trip", lambda: source)
    first["costs"]["total"] = "0"
    first["fuel_stops"].clear()
    source["costs"]["total"] = "999"
    second = cache.get_or_compute("trip", lambda: pytest.fail("cache missed"))
    assert second == {"costs": {"total": "10"}, "fuel_stops": [{"station_id": "csv-1"}]}


def test_failed_distinct_trip_keys_do_not_accumulate_idle_locks():
    cache = ResultCache(max_entries=2)
    for index in range(100):
        with pytest.raises(ValueError):
            cache.get_or_compute(
                str(index), lambda: (_ for _ in ()).throw(ValueError())
            )
    assert not cache._entries
    assert len(cache._locks) <= cache.max_entries


def test_deadline_waiter_does_not_cancel_leader_or_poison_next_request():
    cache, began, release = ResultCache(), Event(), Event()

    def compute():
        began.set()
        assert release.wait(2)
        return {"status": "verified"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(cache.get_or_compute, "same", compute)
        try:
            assert began.wait(1)
            with pytest.raises(PlanningDeadlineExceeded):
                cache.get_or_compute(
                    "same",
                    lambda: pytest.fail("waiter computed"),
                    RequestBudget(deadline_seconds=0.02),
                )
        finally:
            release.set()
        assert leader.result(timeout=2) == {"status": "verified"}
    assert cache.get_or_compute("same", lambda: pytest.fail("cached result lost")) == {
        "status": "verified"
    }


def test_failed_leader_wakes_follower_and_allows_retry():
    cache, began, release = ResultCache(), Event(), Event()

    def fail():
        began.set()
        assert release.wait(2)
        raise RuntimeError("leader failed")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get_or_compute, "trip", fail)
        try:
            assert began.wait(1)
            second = pool.submit(
                cache.get_or_compute, "trip", lambda: {"status": "verified"}
            )
        finally:
            release.set()
        with pytest.raises(RuntimeError):
            first.result(timeout=2)
        assert second.result(timeout=2) == {"status": "verified"}


def test_returned_cache_hit_can_be_mutated_independently_in_parallel():
    cache = ResultCache()
    cache.get_or_compute("trip", lambda: {"fuel_stops": []})

    def mutate(index):
        item = cache.get_or_compute("trip", lambda: pytest.fail("computed again"))
        item["fuel_stops"].append(index)
        return item

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(mutate, range(30)))
    assert all(result["fuel_stops"] == [index] for index, result in enumerate(results))


def test_cached_result_expires_at_exact_ttl_boundary(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("app.api.cache.time.monotonic", lambda: now[0])
    cache = ResultCache(ttl_seconds=10)
    cache.get_or_compute("trip", lambda: {"generation": 1})
    now[0] = 109.999999
    assert cache.get_or_compute("trip", lambda: pytest.fail("early expiry")) == {
        "generation": 1
    }
    now[0] = 110
    assert cache.get_or_compute("trip", lambda: {"generation": 2}) == {"generation": 2}
    assert not cache._locks


def test_evicted_result_can_recompute_without_lock_or_payload_leaks():
    cache = ResultCache(max_entries=2)
    for index in range(50):
        cache.get_or_compute(str(index), lambda index=index: {"index": index})
        assert len(cache._entries) <= 2
        assert not cache._locks
    assert cache.get_or_compute("0", lambda: {"index": "recomputed"}) == {
        "index": "recomputed"
    }


def test_result_completed_after_deadline_is_never_cached():
    now = [100.0]
    budget = RequestBudget(clock=lambda: now[0], deadline_seconds=1)
    cache = ResultCache()

    def compute():
        now[0] = 101
        return {"status": "too late"}

    with pytest.raises(PlanningDeadlineExceeded):
        cache.get_or_compute("trip", compute, budget)
    assert not cache._entries
    assert not cache._locks
    assert cache.get_or_compute("trip", lambda: {"status": "recovered"}) == {
        "status": "recovered"
    }
