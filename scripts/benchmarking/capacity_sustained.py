"""Continuous arrivals exercise worker capacity without draining between requests."""

import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx

from scripts.benchmarking.capacity_metrics import PressureSample
from scripts.nationwide_quality_metrics import percentile


def sustained_load(probe, users, seconds, *, same=False):
    from app.api.deadline import planning_activity

    gate, sequence_guard = threading.Event(), threading.Lock()
    rows, sequence = [], 0
    before, calls = len(probe.observed), probe.calls
    began = time.monotonic()
    deadline = began + seconds

    def worker():
        nonlocal sequence
        with httpx.Client(base_url=probe.url, timeout=20, trust_env=False) as client:
            while time.monotonic() < deadline:
                with sequence_guard:
                    index, sequence = sequence, sequence + 1
                row = probe.request(client, gate, index, same)
                with sequence_guard:
                    rows.append(row)

    with PressureSample() as pressure, ThreadPoolExecutor(max_workers=users) as pool:
        futures = [pool.submit(worker) for _ in range(users)]
        gate.set()
        for future in futures:
            future.result()
    end = time.monotonic() + 5
    while planning_activity()["active_planners"] and time.monotonic() < end:
        time.sleep(0.01)
    drained = planning_activity()["active_planners"] == 0
    successes = [row for row in rows if row["status"] == 200]
    budgets = probe.observed[before:]
    errors = [error for row in rows for error in row["contract_errors"]]
    wall = time.monotonic() - began
    result = {
        "scenario": "continuous_cached" if same else "continuous_distinct",
        "users": users,
        "arrival_window_seconds": seconds,
        "elapsed_seconds": round(wall, 4),
        "requests": len(rows),
        "statuses": dict(Counter(row["status"] for row in rows)),
        "errors": dict(Counter(row["error"] for row in rows if row["error"])),
        "p95_seconds": percentile([row["seconds"] for row in rows], 95),
        "p99_seconds": percentile([row["seconds"] for row in rows], 99),
        "http_queue_p95_seconds": percentile(
            [row["http_queue_seconds"] for row in rows], 95
        ),
        "saved_get_p95_seconds": percentile(
            [
                row["saved_seconds"]
                for row in successes
                if row["saved_seconds"] is not None
            ],
            95,
        ),
        "slow_requests": [
            {
                "seconds": round(row["seconds"], 6),
                "status": row["status"],
                "http_queue_seconds": row["http_queue_seconds"],
                "saved_seconds": row["saved_seconds"],
                "persistence_stages": row["persistence_stages"],
            }
            for row in sorted(rows, key=lambda r: r["seconds"], reverse=True)[:10]
        ],
        "max_seconds": max((row["seconds"] for row in rows), default=0),
        "requests_per_second": round(len(rows) / wall, 3),
        "planner_calls": probe.calls - calls,
        "cache_hits": sum(b.metrics.get("completed_result_reuses", 0) for b in budgets),
        "coalesced": sum(b.metrics.get("coalesced_result_reuses", 0) for b in budgets),
        "replay_attempts": sum(b.attempts for b in budgets),
        "peak": pressure.report(),
        "drained": drained,
        "contract_errors": errors,
        "saved_results": all(row["saved"] for row in successes),
        "fresh_ids": len({row["trip_id"] for row in successes}) == len(successes),
        "fresh_tokens": len({row["token"] for row in successes}) == len(successes),
    }
    result["passed"] = bool(rows) and (
        drained
        and not errors
        and result["saved_results"]
        and result["fresh_ids"]
        and result["fresh_tokens"]
        and result["max_seconds"] < 15
        and pressure.peak["active_planners"] <= 6
        and all(row["status"] == 200 or row["error"] == "PLANNING_BUSY" for row in rows)
        and (not (same or users <= 12) or len(successes) == len(rows))
    )
    return result
