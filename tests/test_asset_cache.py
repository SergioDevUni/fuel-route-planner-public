from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest

from app.planning.asset_cache import ImmutableAssetCache
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget


def test_concurrent_cold_requests_share_one_asset_and_preserve_version() -> None:
    cache = ImmutableAssetCache[object]("test", max_entries=2)
    barrier = Barrier(6)
    calls = 0
    guard = Lock()
    asset = object()

    def load() -> object:
        nonlocal calls
        with guard:
            calls += 1
        return asset

    def request(_: int) -> object:
        barrier.wait(timeout=2)
        return cache.get("version-one", load, RequestBudget())

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(request, range(6)))
    assert all(item is asset for item in results)
    assert calls == 1
    newer = cache.get("version-two", object)
    assert newer is not asset
    assert cache.get("version-one", load) is asset


def test_waiter_deadline_does_not_cancel_asset_owner_or_other_requests() -> None:
    cache = ImmutableAssetCache[object]("test")
    started, release = Event(), Event()
    asset = object()

    def load() -> object:
        started.set()
        assert release.wait(2)
        return asset

    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(cache.get, "version", load, RequestBudget())
        assert started.wait(1)
        try:
            with pytest.raises(PlanningDeadlineExceeded):
                cache.get("version", object, RequestBudget(deadline_seconds=0.02))
            assert not owner.done()
        finally:
            release.set()
        assert owner.result(timeout=1) is asset
    assert cache.get("version", object) is asset


def test_failed_load_can_retry_and_does_not_poison_another_version() -> None:
    cache = ImmutableAssetCache[object]("test")

    def broken() -> object:
        raise TimeoutError("asset loader failed")

    with pytest.raises(TimeoutError, match="asset loader failed"):
        cache.get("broken", broken)
    recovered = cache.get("broken", object)
    assert recovered is cache.get("broken", broken)
    assert recovered is not cache.get("newer", object)


def test_clear_during_load_does_not_republish_the_cleared_generation() -> None:
    cache = ImmutableAssetCache[object]("test")
    started, release = Event(), Event()

    def load() -> object:
        started.set()
        assert release.wait(2)
        return object()

    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(cache.get, "version", load)
        assert started.wait(1)
        cache.clear()
        release.set()
        old = owner.result(timeout=1)
    assert cache.get("version", object) is not old
