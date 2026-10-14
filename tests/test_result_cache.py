import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.api.cache import ResultCache
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget


def test_same_key_concurrent_requests_compute_once() -> None:
    cache = ResultCache()
    barrier = threading.Barrier(8)
    calls = 0
    counter_lock = threading.Lock()

    def compute() -> dict:
        nonlocal calls
        with counter_lock:
            calls += 1
        time.sleep(0.05)
        return {"status": "verified"}

    def request(_: int) -> dict:
        barrier.wait()
        return cache.get_or_compute("same", compute)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(request, range(8)))
    assert calls == 1
    assert all(item["status"] == "verified" for item in results)


def test_failed_computation_is_not_cached() -> None:
    cache = ResultCache()

    def fail() -> dict:
        raise ValueError("upstream failed")

    with pytest.raises(ValueError):
        cache.get_or_compute("key", fail)
    assert cache.get_or_compute("key", lambda: {"status": "verified"}) == {
        "status": "verified"
    }


def test_same_key_wait_respects_request_deadline() -> None:
    cache = ResultCache()
    started = threading.Event()
    release = threading.Event()

    def slow() -> dict:
        started.set()
        assert release.wait(1)
        return {"status": "verified"}

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(cache.get_or_compute, "same", slow)
        assert started.wait(1)
        began = time.monotonic()
        with pytest.raises(PlanningDeadlineExceeded):
            cache.get_or_compute(
                "same",
                lambda: {"status": "unexpected"},
                RequestBudget(deadline_seconds=0.05),
            )
        assert time.monotonic() - began < 0.25
        release.set()
        assert pending.result() == {"status": "verified"}
