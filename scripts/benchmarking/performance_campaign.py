"""Measure the installed release through live Waitress HTTP and offline references."""

import atexit
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from unittest.mock import patch

import httpx

from scripts.atomic_checkpoint import write_checkpoint
from scripts.benchmark_budget import BenchmarkBudget
from scripts.benchmarking.campaign import CONCURRENT_CASES
from scripts.benchmarking.http_measurement import measure_http
from scripts.benchmarking.performance_reporting import initial_report, summarize_report
from scripts.local_common import ROOT, load_local_env, verify_installed
from scripts.reference_scope import decode_graph
from scripts.road_replay import decode_route


class PerformanceCampaign:
    """Own capture state and the sequential, reference, and concurrency stages."""

    def __init__(self, args, parser):
        self.args, self.parser = args, parser
        load_local_env()
        self.manifest = verify_installed()
        self.descriptor = json.loads(
            (ROOT / "local-release.json").read_text(encoding="utf-8")
        )
        for key, value in self.descriptor["hosted_settings"].items():
            os.environ[key] = value
        os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
        if not os.environ.get("ORS_API_KEY", "").strip():
            raise ValueError("ORS_API_KEY is required")

        import django

        django.setup()

        from app.api.assets import load_candidates
        from app.routing.client import OpenRouteServiceAdapter
        from scripts.benchmarking.cases import BENCHMARK_CASES

        self.names = self.args.cases or list(BENCHMARK_CASES)
        if any(name not in BENCHMARK_CASES for name in self.names):
            self.parser.error("unknown benchmark case")
        self.index = load_candidates(
            self.descriptor["dataset"]["source_sha256"],
            self.descriptor["dataset"]["candidate_sha256"],
        )
        self.by_id = {station.station_id: station for station in self.index.stations}
        self.context = threading.local()
        self.guard = threading.Lock()
        self.budgets = {}
        self.routes = {}
        self.current_case = None

        self.original_route = OpenRouteServiceAdapter.route
        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        self.report = initial_report(
            self.names, self.manifest, self.descriptor, self.index, self.args
        )
        self.args.output.parent.mkdir(parents=True, exist_ok=True)
        self.facts_path = self.args.output.with_suffix(".road-facts.json")
        self.resume()

    def set_current_case(self, name):
        self.current_case = name

    def resume(self):
        from scripts.benchmarking.capture import HOSTED_GRAPHS

        if self.args.resume:
            previous = json.loads(self.args.output.read_text())
            for key in (
                "planner_sha256",
                "asset_sha256",
                "candidate_sha256",
                "benchmark_source_sha256",
                "cases",
                "protocol",
            ):
                if previous[key] != self.report[key]:
                    raise ValueError(f"resume identity differs: {key}")
            if previous.get("finished_utc"):
                raise ValueError("benchmark already finished")
            self.report = previous
            self.report.setdefault("resumed_utc", []).append(
                datetime.now(UTC).isoformat()
            )
            if self.facts_path.exists():
                facts = json.loads(self.facts_path.read_text())
                HOSTED_GRAPHS.update(
                    {
                        name: decode_graph(value)
                        for name, value in facts["graphs"].items()
                    }
                )
                from app.domain.routing import Coordinates

                self.routes.update(
                    {
                        name: {
                            tuple(
                                Coordinates(**p) for p in item["points"]
                            ): decode_route(item["route"])
                            for item in values
                        }
                        for name, values in facts["routes"].items()
                    }
                )
        elif self.args.output.exists():
            raise ValueError("output exists; use --resume or a new path")

    def checkpoint(self):
        write_checkpoint(self.args.output, self.report, default=str)

    def drain(self):
        from app.api.deadline import planning_activity

        end = time.monotonic() + 25
        while planning_activity()["active_planners"]:
            if time.monotonic() > end:
                raise RuntimeError("benchmark planners did not drain")
            time.sleep(0.05)

    def reset(self, state, namespace):
        from app.api.cache import trip_cache, verified_incumbent_cache
        from app.planning.baseline_cache import baseline_route_cache
        from app.planning.candidates import CandidateIndex, _prepare_geometry

        self.drain()
        if state != "result_cached":
            trip_cache._entries.clear()
        if state == "routing_cold":
            verified_incumbent_cache._entries.clear()
            baseline_route_cache._entries.clear()
            _prepare_geometry.cache_clear()
            CandidateIndex.along_route.cache_clear()
            os.environ["FUEL_MATRIX_CACHE_VERSION"] = namespace

    def observed_budget(self):
        from app.planning.budget import RequestBudget

        budget = RequestBudget()
        identity = self.context.identity
        with self.guard:
            self.budgets[identity] = budget
        return budget

    def observed_graph(self, *values, **options):
        from app.planning.road_graph import graph_from_matrix
        from scripts.benchmarking.capture import HOSTED_GRAPHS

        graph = graph_from_matrix(*values, **options)
        if self.current_case is not None:
            HOSTED_GRAPHS[self.current_case] = graph
        return graph

    def observed_route(self, adapter, points, budget):
        route = self.original_route(adapter, points, budget)
        if self.current_case is not None:
            with self.guard:
                self.routes.setdefault(self.current_case, {})[tuple(points)] = route
        return route

    def observed_application(self, environ, start_response):
        self.context.identity = environ.get("HTTP_X_BENCHMARK_ID", "health")
        return self.application(environ, start_response)

    def request(self, name, state, trial, phase, barrier=None, concurrency=1):
        return measure_http(
            self.url,
            self.by_id,
            self.budgets,
            name,
            state,
            trial,
            phase,
            barrier,
            concurrency,
        )

    def start(self):
        from django.core.wsgi import get_wsgi_application
        from waitress.server import create_server

        self.application = get_wsgi_application()

        self.channels = {}
        self.server = create_server(
            self.observed_application,
            host="127.0.0.1",
            port=0,
            threads=12,
            map=self.channels,
            expose_tracebacks=False,
        )
        self.io_thread = threading.Thread(target=self.server.run, daemon=True)
        self.io_thread.start()
        self.url = f"http://127.0.0.1:{self.server.effective_port}"
        self.pacing = BenchmarkBudget(self.args.call_ledger, self.args.call_cap)
        self.pacing.__enter__()
        atexit.register(self.pacing.__exit__)

    def sequential(self):
        for name in self.names:
            self.current_case = name
            for trial in range(1, self.args.repeats + 1):
                completed = [
                    row
                    for row in self.report["rows"]
                    if row["case"] == name and row["trial"] == trial
                ]
                if len(completed) == 3:
                    continue
                if completed:
                    self.report.setdefault("superseded_partial_trials", []).extend(
                        completed
                    )
                    self.report["rows"] = [
                        row
                        for row in self.report["rows"]
                        if not (row["case"] == name and row["trial"] == trial)
                    ]
                for state in ("routing_cold", "routing_warm", "result_cached"):
                    self.pacing.before_request(4 if state != "result_cached" else 0)
                    self.reset(state, f"release-benchmark-{self.run_id}-{name}-{trial}")
                    row = self.request(name, state, trial, "sequential")
                    self.pacing.record(row["hosted_attempts"])
                    self.report["rows"].append(row)
                    self.checkpoint()
                    print(
                        json.dumps(
                            {
                                "phase": "sequential",
                                "case": name,
                                "state": state,
                                "trial": trial,
                                "status": row["http_status"],
                                "seconds": row["seconds"],
                                "valid": row["contract_ok"],
                            }
                        ),
                        flush=True,
                    )

    def concurrent(self):
        if not self.args.skip_concurrency:
            load_names = [name for name in CONCURRENT_CASES if name in self.names]
            for concurrency in self.args.concurrency:
                if len(load_names) % concurrency:
                    raise ValueError("load cases must form complete concurrent batches")
                for offset in range(0, len(load_names), concurrency):
                    batch = load_names[offset : offset + concurrency]
                    for state in ("routing_cold", "routing_warm", "result_cached"):
                        if any(
                            item["concurrency"] == concurrency
                            and item["cache_state"] == state
                            and item["cases"] == batch
                            for item in self.report["batches"]
                        ):
                            continue
                        self.pacing.before_request(
                            4 * concurrency if state != "result_cached" else 0
                        )
                        self.reset(
                            state, f"release-load-{self.run_id}-{concurrency}-{offset}"
                        )
                        barrier = threading.Barrier(concurrency)
                        began = time.perf_counter()
                        with ThreadPoolExecutor(max_workers=concurrency) as pool:
                            futures = [
                                pool.submit(
                                    self.request,
                                    name,
                                    state,
                                    1,
                                    "concurrent",
                                    barrier,
                                    concurrency,
                                )
                                for name in batch
                            ]
                            rows = [future.result() for future in futures]
                        elapsed = time.perf_counter() - began
                        self.pacing.record(sum(row["hosted_attempts"] for row in rows))
                        self.report["concurrent_rows"].extend(rows)
                        self.report["batches"].append(
                            {
                                "concurrency": concurrency,
                                "cache_state": state,
                                "cases": batch,
                                "seconds": round(elapsed, 4),
                                "requests": len(rows),
                                "verified_successes": sum(
                                    row["contract_ok"] for row in rows
                                ),
                            }
                        )
                        self.checkpoint()
                        print(
                            json.dumps(
                                {
                                    "phase": "concurrent",
                                    "users": concurrency,
                                    "state": state,
                                    "valid": sum(row["contract_ok"] for row in rows),
                                    "requests": len(rows),
                                    "seconds": round(elapsed, 3),
                                }
                            ),
                            flush=True,
                        )

    def close(self):
        from waitress import wasyncore

        from app.api.deadline import shutdown_planning
        from scripts.benchmarking.capture import HOSTED_GRAPHS

        self.drain()
        shutdown_planning(timeout=20)
        self.server.task_dispatcher.shutdown(timeout=5)
        wasyncore.close_all(map=self.channels)
        self.io_thread.join(timeout=2)
        facts = {
            "graphs": {name: asdict(graph) for name, graph in HOSTED_GRAPHS.items()},
            "routes": {
                name: [
                    {"points": asdict_points(points), "route": asdict(route)}
                    for points, route in values.items()
                ]
                for name, values in self.routes.items()
            },
        }
        private = self.facts_path
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text(json.dumps(facts, default=str) + "\n", encoding="utf-8")
        self.report["road_facts_sha256"] = hashlib.sha256(
            private.read_bytes()
        ).hexdigest()
        self.checkpoint()

    def summarize(self):
        summarize_report(self.report, self.args.concurrency)
        self.checkpoint()
        print(
            json.dumps(
                {
                    "finished": str(self.args.output),
                    "summary": self.report["sequential_summary"],
                    "quality": self.report["comparison_summary"],
                }
            ),
            flush=True,
        )

    def run(self):
        from app.routing.client import OpenRouteServiceAdapter
        from scripts.benchmarking.performance_references import collect_references

        def route(adapter, points, budget):
            return self.observed_route(adapter, points, budget)

        self.start()
        try:
            with (
                patch("app.api.views.RequestBudget", self.observed_budget),
                patch(
                    "app.planning.matrix_selection.graph_from_matrix",
                    self.observed_graph,
                ),
                patch.object(OpenRouteServiceAdapter, "route", route),
            ):
                with httpx.Client(base_url=self.url, trust_env=False) as client:
                    assert client.get("/health/ready").status_code == 200
                self.sequential()
                self.drain()
                if not self.args.skip_references:
                    collect_references(
                        self.names,
                        self.report,
                        self.index,
                        self.routes,
                        self.by_id,
                        self.pacing,
                        self.checkpoint,
                        self.set_current_case,
                    )
                self.current_case = None
                self.concurrent()
        finally:
            self.close()
        self.summarize()


def run_campaign(args, parser) -> None:
    PerformanceCampaign(args, parser).run()


def asdict_points(points):
    return [asdict(point) for point in points]
