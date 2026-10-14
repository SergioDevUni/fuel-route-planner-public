import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx
import pytest

from app.planning.budget import RequestBudget
from app.routing.client import OpenRouteServiceAdapter, RoutingQuotaExceeded
from scripts.benchmark_budget import BenchmarkBudget, BenchmarkPaused


def test_cap_counts_failures_and_survives_restart(tmp_path):
    path = tmp_path / "calls.json"
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(500, json={}))
    )
    adapter = OpenRouteServiceAdapter("fake-test-key", client)
    for _ in range(2):
        with BenchmarkBudget(path, limit=2):
            with pytest.raises(RuntimeError):
                adapter._post("https://example.test", {}, RequestBudget())
    with BenchmarkBudget(path, limit=2):
        with pytest.raises(BenchmarkPaused, match="cap"):
            adapter._post("https://example.test", {}, RequestBudget())
    assert json.loads(path.read_text())["attempts"] == 2
    client.close()


def test_concurrent_calls_cannot_exceed_cap(tmp_path):
    path = tmp_path / "calls.json"
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    )
    adapter = OpenRouteServiceAdapter("fake-test-key", client)
    with BenchmarkBudget(path, limit=3):

        def attempt(_):
            try:
                adapter._post("https://example.test", {}, RequestBudget())
                return True
            except BenchmarkPaused:
                return False

        with ThreadPoolExecutor(max_workers=6) as pool:
            assert sum(pool.map(attempt, range(10))) == 3
    assert json.loads(path.read_text())["attempts"] == 3
    client.close()


def test_provider_quota_pauses_next_measurement(tmp_path):
    path = tmp_path / "calls.json"
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(429, json={}))
    )
    adapter = OpenRouteServiceAdapter("fake-test-key", client)
    with BenchmarkBudget(path):
        with pytest.raises(RoutingQuotaExceeded):
            adapter._post("https://example.test", {}, RequestBudget())
    with pytest.raises(BenchmarkPaused, match="provider quota"):
        BenchmarkBudget(path).before_request()
    client.close()


def test_pacing_waits_before_measurement(tmp_path):
    pacing = BenchmarkBudget(tmp_path / "calls.json", per_minute=4)
    pacing.data["calls"] = [100.0]
    clock = [100.0]

    def sleep(seconds):
        clock[0] += seconds

    with (
        patch("scripts.benchmark_budget.time.time", lambda: clock[0]),
        patch("scripts.benchmark_budget.time.sleep", sleep),
    ):
        pacing.before_request(4)
    assert clock[0] >= 161
