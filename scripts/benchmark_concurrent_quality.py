"""Measure actual loopback HTTP planning with three concurrent users.

This is a local threaded WSGI deployment, not a production hosting benchmark.
Provider pacing stays between batches, outside request timing. No credentials,
response bodies or result tokens are persisted.
"""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from socketserver import ThreadingMixIn
from threading import Barrier, Lock, Thread, local
from time import perf_counter
from unittest.mock import patch
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import httpx
from django.core.wsgi import get_wsgi_application

from app.api.cache import verified_incumbent_cache
from app.planning.candidates import CandidateIndex, _prepare_geometry
from scripts.benchmarking.cases import BENCHMARK_CASES
from scripts.benchmarking.measurement import (
    GRAPH_VERSION,
    ROOT,
    DatasetVersion,
    ProviderPacing,
    RequestBudget,
    baseline_route_cache,
    local_baseline_route_cache,
    routing_key,
    trip_cache,
    validate_verified_trip,
)
from scripts.nationwide_quality_metrics import cost_gap, summarize
from scripts.road_replay import RoadReplay


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    request_queue_size = 128


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "var/benchmarks/2026-10-11-generic-concurrent-quality.json",
    )
    parser.add_argument("--references", type=Path)
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=tuple(BENCHMARK_CASES),
        default=[
            "santa_fe_oklahoma_city",
            "las_vegas_san_diego",
            "san_antonio_new_orleans",
            "detroit_cincinnati",
            "boise_spokane",
            "fargo_sioux_falls",
        ],
    )
    parser.add_argument("--concurrency", type=int, choices=(1, 3, 6, 12, 24), default=3)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--record-roads", type=Path)
    parser.add_argument("--replay-roads", type=Path)
    parser.add_argument("--portfolio-first", action="store_true")
    parser.add_argument("--reset-assets", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    if args.concurrency > 6 and args.replay_roads is None:
        parser.error("12/24-user tests require road replay to respect provider pacing")
    if len(args.cases) % args.concurrency:
        parser.error("case count must be a multiple of concurrent users")
    os.environ.update(
        {
            "ORS_API_KEY": "recorded-roads-no-network"
            if args.replay_roads
            else routing_key(),
            "FUEL_NATIONAL_LOCAL_ROUTER": "0",
            "FUEL_PARTIAL_ROAD_LOCAL_FIRST": "0",
            "FUEL_MATRIX_GRAPH": "1",
            "FUEL_MATRIX_FIRST": "1",
            "FUEL_NATIONAL_VALHALLA_URL": "http://127.0.0.1:8005",
            "FUEL_NATIONAL_VALHALLA_VERSION": GRAPH_VERSION,
        }
    )
    active = DatasetVersion.objects.get(status="active")
    index = CandidateIndex(ROOT / "var/station_candidates", active.source_sha256)
    by_id = {station.station_id: station for station in index.stations}
    application = get_wsgi_application()
    context = local()
    lock = Lock()
    budgets = {}

    def observed_budget():
        budget = RequestBudget()
        with lock:
            budgets[context.name] = budget
        return budget

    def observed_application(environ, start_response):
        context.name = environ.get("HTTP_X_BENCHMARK_ID", "unknown")
        return application(environ, start_response)

    server = make_server(
        "127.0.0.1",
        0,
        observed_application,
        server_class=ThreadedServer,
        handler_class=QuietHandler,
    )
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    from app.api.deadline import planning_capacity

    report = {
        "created_utc": datetime.now(UTC).isoformat(),
        "planning_capacity": planning_capacity(),
        "logical_processors": os.cpu_count(),
        "environment": (
            f"loopback HTTP, threaded WSGI, {args.concurrency} simultaneous users; "
            "provider cache uncontrolled; builder unchanged"
        ),
        "candidate_version": index.version,
        "price_source_sha256": active.source_sha256,
        "online_planning_ceiling_seconds": 15,
        "online_target_seconds": 10,
        "online_target_is_acceptance_gate": False,
        "concurrency": args.concurrency,
        "road_source": "recorded facts and observed delays"
        if args.replay_roads
        else "live",
        "asset_state": "reset each cold batch"
        if args.reset_assets
        else "startup then retained",
        "repeats": args.repeats,
        "cost_quality_role": "accepted purchase-cost quality; regression evidence only",
        "historical_99_percent_cost_target_is_delivery_gate": False,
        "rows": [],
    }
    pacing = ProviderPacing()
    batches = tuple(
        tuple(args.cases[index : index + args.concurrency])
        for index in range(0, len(args.cases), args.concurrency)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint():
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def request(name, mode, state, barrier, position):
        case = BENCHMARK_CASES[name]
        identity = f"{mode}/{state}/{name}/{position}"
        body = {
            "start": case.start,
            "finish": case.finish,
            "initial_fuel_gallons": case.initial,
            "reserve_gallons": case.reserve,
            # Different valuation inputs prevent same-key coalescing from
            # disguising independent load; purchase optimization is unchanged.
            "initial_fuel_price_per_gallon": str(
                Decimal("3.20") + Decimal(position) / 100
            ),
            "optimization_mode": "balanced",
            "max_extra_duration_minutes": "30",
        }
        with httpx.Client(timeout=20, trust_env=False) as client:
            barrier.wait(timeout=10)
            began = perf_counter()
            try:
                response = client.post(
                    f"http://127.0.0.1:{server.server_port}/api/v1/trips",
                    json=body,
                    headers={"X-Benchmark-Id": identity},
                )
                payload = response.json()
                row = {
                    "case": name,
                    "mode": mode,
                    "cache_state": state,
                    "seconds": round(perf_counter() - began, 3),
                    "http_status": response.status_code,
                    "retry_after": response.headers.get("Retry-After"),
                    "contract_ok": False,
                    "error": payload.get("error", {}).get("code"),
                }
                if response.status_code == 200:
                    errors = validate_verified_trip(payload, set(by_id))
                    for stop in payload["fuel_stops"]:
                        if (
                            stop["station_id"] in by_id
                            and Decimal(stop["price_per_gallon"])
                            != by_id[stop["station_id"]].price_per_gallon
                        ):
                            errors.append("selected price differs from CSV")
                    row.update(
                        contract_ok=not errors,
                        contract_errors=errors,
                        purchase_cost=payload["costs"]["fuel_purchases_cost"],
                        station_ids=[
                            stop["station_id"] for stop in payload["fuel_stops"]
                        ],
                        selection=payload["assumptions"]["station_selection"],
                    )
                    with lock:
                        budget = budgets.get(identity)
                    profile = budget.verified_routing_profile if budget else None
                    row["profile"] = {
                        "local": "valhalla",
                        "hosted": "ors_fastest_driving_car",
                    }.get(profile)
            except Exception as error:
                row = {
                    "case": name,
                    "mode": mode,
                    "cache_state": state,
                    "seconds": round(perf_counter() - began, 3),
                    "http_status": None,
                    "contract_ok": False,
                    "error": type(error).__name__,
                }
        with lock:
            budget = budgets.get(identity)
        row["hosted_attempts"] = budget.attempts if budget else 0
        row["stage_seconds"] = budget.stage_seconds if budget else {}
        row["completed_stage_cpu_seconds"] = (
            budget.completed_stage_cpu_seconds if budget else {}
        )
        row["work_metrics"] = budget.metrics if budget else {}
        row["offer_audit"] = budget.offer_audit if budget else {}
        row["first_verified_seconds"] = (
            budget.first_verified_seconds if budget else None
        )
        row["cost_trajectory"] = budget.cost_trajectory if budget else []
        row["first_verified_from_cache"] = (
            budget.first_verified_from_cache if budget else None
        )
        return row

    try:
        with (
            patch("app.api.views.RequestBudget", observed_budget),
            RoadReplay(args.record_roads, args.replay_roads),
        ):
            order = (
                ("portfolio", "hosted_default")
                if args.portfolio_first
                else ("hosted_default", "portfolio")
            )
            for mode in order:
                os.environ["FUEL_PARTIAL_ROAD_REFINER"] = (
                    "1" if mode == "portfolio" else "0"
                )
                os.environ["FUEL_PARTIAL_ROAD_PORTFOLIO"] = (
                    "1" if mode == "portfolio" else "0"
                )
                for number, names in enumerate(batches * args.repeats):
                    os.environ["FUEL_MATRIX_CACHE_VERSION"] = (
                        f"concurrent-{datetime.now(UTC).timestamp()}-{mode}-{number}"
                    )
                    for state in ("routing_cold", "routing_warm"):
                        if args.replay_roads is None:
                            pacing.before_request(reserve_attempts=4 * args.concurrency)
                        trip_cache._entries.clear()
                        if state == "routing_cold":
                            if args.reset_assets:
                                from app.api.assets import (
                                    _boundary_assets,
                                    _candidate_assets,
                                )

                                _boundary_assets.clear()
                                _candidate_assets.clear()
                            verified_incumbent_cache._entries.clear()
                            _prepare_geometry.cache_clear()
                            CandidateIndex.along_route.cache_clear()
                            baseline_route_cache._entries.clear()
                            local_baseline_route_cache._entries.clear()
                        barrier = Barrier(args.concurrency)
                        with ThreadPoolExecutor(
                            max_workers=args.concurrency
                        ) as executor:
                            futures = [
                                executor.submit(
                                    request, name, mode, state, barrier, position
                                )
                                for position, name in enumerate(names)
                            ]
                            rows = [future.result() for future in futures]
                        pacing.record(sum(row["hosted_attempts"] for row in rows))
                        report["rows"].extend(rows)
                        checkpoint()
                        print(json.dumps({"batch": rows}), flush=True)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    if args.references:
        comparison = json.loads(args.references.read_text(encoding="utf-8"))
        if comparison["candidate_version"] != index.version:
            raise ValueError("reference dataset changed")
        references = {row["case"]: row for row in comparison["references"]}
        report["reference_file"] = str(args.references)
        for row in report["rows"]:
            reference = references.get(row["case"])
            declared = comparison.get("cases", {}).get(row["case"])
            actual = asdict(BENCHMARK_CASES[row["case"]])
            if (
                row["contract_ok"]
                and row.get("profile") == "ors_fastest_driving_car"
                and reference
                and "reference_cost" in reference
                and declared is not None
                and all(
                    declared.get(field) == actual[field]
                    for field in ("start", "finish", "initial", "reserve")
                )
            ):
                row["cost_gap"] = cost_gap(
                    row["purchase_cost"], reference["reference_cost"]
                )
                row["independent_reference_available"] = reference[
                    "independent_reference_available"
                ]
    report["summary"] = {
        f"{mode}/{state}": summarize(
            [
                row
                for row in report["rows"]
                if row["mode"] == mode and row["cache_state"] == state
            ]
        )
        for mode in ("hosted_default", "portfolio")
        for state in ("routing_cold", "routing_warm")
    }
    checkpoint()
    print(json.dumps({"summary": report["summary"]}), flush=True)


if __name__ == "__main__":
    main()
