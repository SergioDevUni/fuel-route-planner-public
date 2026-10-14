"""Disposable HTTP capacity harness with synthetic faults and captured-road replay."""

import hashlib
import json
import logging
import platform
import sqlite3
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

import httpx

from scripts.benchmarking.campaign import OFFLINE_CONCURRENCY
from scripts.benchmarking.fingerprints import benchmark_sources
from scripts.benchmarking.offline_replay import calculate_replay
from scripts.local_common import ROOT, planner_hash, sha256
from scripts.nationwide_quality_metrics import percentile


class CapacityProbe:
    def __init__(
        self,
        database,
        *,
        replays=None,
        admission_wait=4.0,
        threads=32,
        sustained_seconds=20,
    ):
        import django
        from django.conf import settings

        django.setup()
        from django.core.management import call_command
        from django.db import connections

        from app.api.cache import ResultCache
        from app.api.coalescing import RequestCoalescer
        from app.planning.settings import planner_settings

        connections.close_all()
        settings.DATABASES["default"]["NAME"] = database
        connections["default"].settings_dict["NAME"] = database
        settings.FUEL_API_RATE_REQUESTS = 100_000
        call_command("migrate", verbosity=0)
        self.database = database
        self.replays = replays
        self.settings = replace(
            planner_settings(), admission_wait_seconds=admission_wait
        )
        self.cache, self.coalescer = ResultCache(max_entries=256), RequestCoalescer()
        self.guard = threading.Lock()
        self.observed, self.rows = [], []
        self.calls = 0
        self.mode = {"delay": 0.3, "error": False, "deadline": 15.0, "replay": False}
        self.csv_ids = {v.station_id for v in replays[0].stations} if replays else set()
        self.threads = threads
        self.sustained_seconds = sustained_seconds
        self.continuous_rows = []
        self.started = datetime.now(UTC).isoformat()
        self.planner_identity = planner_hash(ROOT)
        self.harness_identity = benchmark_sources(ROOT)

    def key(self, trip, budget):
        request = {
            "start": trip.start,
            "finish": trip.finish,
            "initial": str(trip.initial_fuel_gallons),
            "reserve": str(trip.reserve_gallons),
            "price": str(trip.initial_fuel_price_per_gallon),
        }
        identity = hashlib.sha256(
            json.dumps(request, sort_keys=True).encode()
        ).hexdigest()
        return identity, "offline", "offline"

    def budget(self):
        from app.planning.budget import RequestBudget

        value = RequestBudget(
            deadline_seconds=self.mode["deadline"], settings=self.settings
        )
        with self.guard:
            self.observed.append(value)
        return value

    def calculate(self, trip, budget):
        from app.routing.client import RoutingTransportUnavailable

        with self.guard:
            self.calls += 1
        if self.mode["replay"]:
            index, cases = self.replays
            budget.set_completed_payload_sink(
                lambda payload: self.cache.put_completed(
                    self.key(trip, budget)[0], payload
                )
            )
            case = next(
                v
                for v in cases.values()
                if v.start.coordinates.latitude == trip.start["latitude"]
                and v.start.coordinates.longitude == trip.start["longitude"]
                and v.finish.coordinates.latitude == trip.finish["latitude"]
                and v.finish.coordinates.longitude == trip.finish["longitude"]
            )
            payload = calculate_replay(replace(case, trip=trip), index, budget)
        else:
            with budget.stage("synthetic_planning"):
                time.sleep(self.mode["delay"])
                budget.check_deadline()
                if self.mode["error"]:
                    raise RoutingTransportUnavailable("synthetic provider failure")
            payload = {"status": "verified", "fuel_stops": [], "assumptions": {}}
        self.cache.put_completed(self.key(trip, budget)[0], payload)
        return payload

    def start(self):
        from django.core.wsgi import get_wsgi_application
        from waitress.channel import HTTPChannel
        from waitress.server import create_server

        self.channels, context = {}, threading.local()
        application = get_wsgi_application()

        class TimedChannel(HTTPChannel):
            def service(channel):
                context.queue = time.perf_counter() - channel.probe_queued_at
                return super().service()

        def timed_app(environ, start_response):
            def start(status, headers, exc_info=None):
                headers.append(("X-Probe-Queue-Seconds", str(context.queue)))
                return start_response(status, headers, exc_info)

            return application(environ, start)

        self.server = create_server(
            timed_app,
            host="127.0.0.1",
            port=0,
            threads=self.threads,
            map=self.channels,
            expose_tracebacks=False,
        )
        self.server.channel_class = TimedChannel
        add_task = self.server.task_dispatcher.add_task

        def enqueue(task):
            task.probe_queued_at = time.perf_counter()
            return add_task(task)

        self.server.task_dispatcher.add_task = enqueue
        self.url = f"http://127.0.0.1:{self.server.effective_port}"
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()

    def body(self, index, same):
        if self.mode["replay"]:
            cases = list(self.replays[1].values())
            case = cases[0 if same else index % len(cases)]

            def point(p):
                return {
                    "type": "coordinates",
                    "latitude": p.latitude,
                    "longitude": p.longitude,
                }

            return {
                "start": point(case.start.coordinates),
                "finish": point(case.finish.coordinates),
                "initial_fuel_gallons": str(case.trip.initial_fuel_gallons),
                "reserve_gallons": str(case.trip.reserve_gallons),
                "initial_fuel_price_per_gallon": str(
                    Decimal("3.20") + (0 if same else Decimal(index) / 100)
                ),
            }
        return {
            "start": {
                "type": "coordinates",
                "latitude": 40,
                "longitude": -90 + (0 if same else index / 100),
            },
            "finish": {"type": "coordinates", "latitude": 40, "longitude": -80},
        }

    def request(self, client, barrier, index, same):
        from scripts.benchmarking.contracts import validate_verified_trip

        body = self.body(index, same)
        barrier.wait(timeout=5)
        began = time.perf_counter()
        response = client.post("/api/v1/trips", json=body)
        seconds = time.perf_counter() - began
        payload, saved, errors = response.json(), False, []
        saved_seconds = None
        if response.status_code == 200:
            saved_began = time.perf_counter()
            fetched = client.get(
                f"/api/v1/trips/{payload['trip_id']}",
                headers={"Authorization": "Bearer " + payload["result_token"]},
            )
            saved_seconds = time.perf_counter() - saved_began
            saved = fetched.status_code == 200
            if self.mode["replay"]:
                errors = validate_verified_trip(payload, self.csv_ids)
                prices = {
                    v.station_id: v.price_per_gallon for v in self.replays[0].stations
                }
                errors += [
                    "CSV price mismatch"
                    for stop in payload["fuel_stops"]
                    if Decimal(stop["price_per_gallon"]) != prices[stop["station_id"]]
                ]
        return {
            "status": response.status_code,
            "seconds": seconds,
            "error": payload.get("error", {}).get("code"),
            "saved": saved,
            "contract_errors": errors,
            "trip_id": payload.get("trip_id"),
            "token": payload.get("result_token"),
            "http_queue_seconds": float(response.headers["X-Probe-Queue-Seconds"]),
            "saved_seconds": saved_seconds,
            "persistence_stages": json.loads(
                response.headers.get("X-Probe-Persistence-Stages", "{}")
            ),
        }

    def wave(self, users, label, same=False):
        from app.api.deadline import planning_activity
        from scripts.benchmarking.capacity_metrics import PressureSample

        before, calls = len(self.observed), self.calls
        barrier = threading.Barrier(users)
        with httpx.Client(base_url=self.url, timeout=20, trust_env=False) as client:
            with (
                PressureSample() as pressure,
                ThreadPoolExecutor(max_workers=users) as pool,
            ):
                futures = [
                    pool.submit(self.request, client, barrier, i, same)
                    for i in range(users)
                ]
                time.sleep(0.02)
                began = time.perf_counter()
                health = client.get("/health/live")
                health_seconds = time.perf_counter() - began
                rows = [future.result() for future in futures]
        end = time.monotonic() + 5
        while planning_activity()["active_planners"] and time.monotonic() < end:
            time.sleep(0.01)
        assert not planning_activity()["active_planners"]
        successes = [row for row in rows if row["status"] == 200]
        budgets = self.observed[before:]
        result = {
            "scenario": label,
            "users": users,
            "statuses": dict(Counter(row["status"] for row in rows)),
            "errors": dict(Counter(row["error"] for row in rows if row["error"])),
            "p50_seconds": percentile([r["seconds"] for r in rows], 50),
            "p95_seconds": percentile([r["seconds"] for r in rows], 95),
            "max_seconds": round(max(r["seconds"] for r in rows), 4),
            "planner_calls": self.calls - calls,
            "cache_hits": sum(
                b.metrics.get("completed_result_reuses", 0) for b in budgets
            ),
            "coalesced": sum(
                b.metrics.get("coalesced_result_reuses", 0) for b in budgets
            ),
            "replay_attempts": sum(b.attempts for b in budgets),
            "max_attempts_per_request": max((b.attempts for b in budgets), default=0),
            "health_status": health.status_code,
            "health_seconds": round(health_seconds, 4),
            "http_queue_p95_seconds": percentile(
                [r["http_queue_seconds"] for r in rows], 95
            ),
            "admission_wait_p95_seconds": percentile(
                [b.metrics.get("planning_admission_wait_seconds", 0) for b in budgets],
                95,
            ),
            "planner_start_wait_p95_seconds": percentile(
                [b.metrics.get("planning_worker_queue_seconds", 0) for b in budgets], 95
            ),
            "replay_missing_queries": sum(
                b.metrics.get("road_replay_missing", 0) for b in budgets
            ),
            "stages_p95_seconds": {
                name: percentile([b.stage_seconds.get(name, 0) for b in budgets], 95)
                for name in {name for b in budgets for name in b.stage_seconds}
            },
            "planner_cpu_seconds": round(
                sum(
                    b.completed_stage_cpu_seconds.get(
                        "offline_planner_replay"
                        if self.mode["replay"]
                        else "synthetic_planning",
                        0,
                    )
                    for b in budgets
                ),
                4,
            ),
            "peak": pressure.report(),
            "fresh_ids": len({r["trip_id"] for r in successes}) == len(successes),
            "fresh_tokens": len({r["token"] for r in successes}) == len(successes),
            "saved_results": all(r["saved"] for r in successes),
            "contract_errors": [error for r in rows for error in r["contract_errors"]],
            "drained": True,
        }
        self.rows.append(result)
        return result

    def synthetic_waves(self):
        for users in OFFLINE_CONCURRENCY:
            self.cache._entries.clear()
            self.wave(users, "distinct_cold")
            self.cache._entries.clear()
            row = self.wave(users, "same_cold", same=True)
            assert row["planner_calls"] == 1 and row["statuses"] == {200: users}
            with patch(
                "app.api.deadline._planning_slots", threading.BoundedSemaphore(0)
            ):
                row = self.wave(users, "cached_full_pool", same=True)
            assert row["statuses"] == {200: users} and row["planner_calls"] == 0

    def faults(self):
        self.cache._entries.clear()
        self.mode.update(delay=4.5)
        row = self.wave(24, "saturation")
        assert row["statuses"].get(503, 0) > 0 and row["max_seconds"] < 6
        self.cache._entries.clear()
        self.mode.update(delay=0.3, error=True)
        assert self.wave(6, "provider_error")["errors"] == {"ROUTING_UNAVAILABLE": 6}
        self.mode.update(error=False, deadline=0.15)
        assert self.wave(6, "slow_timeout")["errors"] == {"PLANNING_TIMEOUT": 6}
        self.mode.update(deadline=15.0)
        with closing(sqlite3.connect(self.database)) as locked:
            locked.execute("BEGIN IMMEDIATE")
            row = self.wave(1, "storage_lock", same=True)
            locked.rollback()
        assert row["errors"] == {"STORAGE_UNAVAILABLE": 1}

    def replay_waves(self):
        self.mode["replay"] = True
        for users in OFFLINE_CONCURRENCY:
            self.cache._entries.clear()
            row = self.wave(users, "recorded_roads_cold")
            if users <= 12:
                assert row["statuses"] == {200: users}, row
                assert row["max_seconds"] < 15, row
        for trial in range(3):
            self.cache._entries.clear()
            row = self.wave(12, f"recorded_roads_sustained_{trial + 1}")
            assert row["statuses"] == {200: 12} and row["max_seconds"] < 15, row
        with patch("app.api.deadline._planning_slots", threading.BoundedSemaphore(0)):
            row = self.wave(12, "recorded_roads_cached_full_pool")
            assert row["statuses"] == {200: 12} and row["planner_calls"] == 0, row

    def run(self):
        from django.db import connections
        from waitress import wasyncore

        from app.api.deadline import shutdown_planning
        from scripts.benchmarking.capacity_environment import resource_environment

        self.start()
        failure = None
        logging.disable(logging.CRITICAL)
        try:
            with ExitStack() as stack:
                for name, value in {
                    "trip_cache": self.cache,
                    "request_coalescer": self.coalescer,
                    "calculation_keys": self.key,
                    "calculate": self.calculate,
                    "RequestBudget": self.budget,
                }.items():
                    stack.enter_context(patch("app.api.views." + name, value))
                self.synthetic_waves()
                self.faults()
                if self.replays:
                    self.replay_waves()
                from scripts.benchmarking.capacity_sustained import sustained_load

                self.cache._entries.clear()
                self.continuous_rows.append(
                    sustained_load(self, 6, self.sustained_seconds)
                )
                self.continuous_rows.append(
                    sustained_load(self, 24, self.sustained_seconds, same=True)
                )
                assert all(row["passed"] for row in self.continuous_rows)
                assert all(
                    r["saved_results"]
                    and r["fresh_ids"]
                    and r["fresh_tokens"]
                    and not r["contract_errors"]
                    and r["health_status"] == 200
                    and r["peak"]["active_planners"] <= 6
                    and r["max_attempts_per_request"] <= 4
                    for r in self.rows
                )
        except Exception as error:
            # Preserve measurements from failed capacity checks for review.
            failure = {
                "type": type(error).__name__,
                "scenario": self.rows[-1]["scenario"] if self.rows else "startup",
            }
        finally:
            drained = shutdown_planning(timeout=5)
            self.server.task_dispatcher.shutdown(timeout=3)
            wasyncore.close_all(map=self.channels)
            self.thread.join(timeout=2)
            connections.close_all()
            logging.disable(logging.NOTSET)
        unchanged = self.planner_identity == planner_hash(
            ROOT
        ) and self.harness_identity == benchmark_sources(ROOT)
        return {
            "schema": 2,
            "passed": failure is None and drained and unchanged,
            "failure": failure,
            "measurement_sources_unchanged": unchanged,
            "started_utc": self.started,
            "finished_utc": datetime.now(UTC).isoformat(),
            "planner_sha256": self.planner_identity,
            "benchmark_source_sha256": self.harness_identity,
            "environment": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "django": version("django"),
                "waitress": version("waitress"),
                "lock_sha256": sha256(ROOT / "uv.lock"),
                "resources": resource_environment(),
            },
            "hosted_attempts": 0,
            "transport": "real Waitress HTTP; disposable SQLite; single process",
            "waitress_threads": self.threads,
            "planning_capacity": 6,
            "admission_wait_seconds": self.settings.admission_wait_seconds,
            "synthetic_delay_seconds": 0.3,
            "shutdown_drained": drained,
            "rows": self.rows,
            "continuous_rows": self.continuous_rows,
        }


def run_probe(
    database: Path,
    *,
    facts_path=None,
    admission_wait=4.0,
    threads=32,
    sustained_seconds=20,
):
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    replays = None
    if facts_path:
        import django

        django.setup()
        from scripts.benchmarking.offline_replay import load_replays

        replays = load_replays(ROOT, facts_path)
        index, cases = replays
        # The baseline retains this captured input's fuel-range outcome in the
        # behavior replay, while capacity measures independently feasible trips.
        replays = index, {k: v for k, v in cases.items() if k != "oakland_fresno"}
    probe = CapacityProbe(
        database,
        replays=replays,
        admission_wait=admission_wait,
        threads=threads,
        sustained_seconds=sustained_seconds,
    )
    with patch("app.api.deadline._planning_slots", threading.BoundedSemaphore(6)):
        report = probe.run()
    if facts_path:
        report["road_facts_sha256"] = sha256(facts_path)
        report["replay_cases"] = list(replays[1])
        report["replay_scope"] = (
            "Captured road directions and normalized directed graphs; "
            "no live I/O timing; matrix snaps conservatively use policy limits. "
            "Repeated routes have distinct starting fuel valuation inputs."
        )
        report["excluded_capacity_cases"] = {
            "oakland_fresno": (
                "Known infeasible captured input; covered by behavior replay."
            )
        }
    return report
