"""Measure a frozen state-stratified sample after independent route preflight."""

import argparse
import atexit
import hashlib
import json
import os
import platform
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import httpx

from scripts.atomic_checkpoint import write_checkpoint
from scripts.benchmark_budget import BenchmarkBudget
from scripts.benchmarking.fingerprints import benchmark_sources
from scripts.feasibility_evidence import classify, outcome_summary
from scripts.local_common import ROOT, load_local_env, planner_hash, verify_installed
from scripts.nationwide_preflight import preflight_case
from scripts.performance_metrics import http_summary


def save(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_checkpoint(path, document)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample", type=Path, default=ROOT / "tests/fixtures/nationwide-sample.json"
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--call-ledger",
        type=Path,
        default=ROOT / "var/verification/benchmark-calls.json",
    )
    parser.add_argument("--call-cap", type=int, default=1000)
    parser.add_argument("--stop-at-calls", type=int)
    args = parser.parse_args()
    load_local_env()
    manifest = verify_installed()
    descriptor = json.loads((ROOT / "local-release.json").read_text(encoding="utf-8"))
    for key, value in descriptor["hosted_settings"].items():
        os.environ[key] = value
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django

    django.setup()
    from django.core.wsgi import get_wsgi_application
    from waitress import wasyncore
    from waitress.server import create_server

    from app.api.assets import load_candidates
    from app.api.cache import trip_cache, verified_incumbent_cache
    from app.api.deadline import planning_activity, shutdown_planning
    from app.api.inputs import resolve_inputs
    from app.api.validation import TripInput
    from app.planning.baseline_cache import baseline_route_cache
    from app.planning.budget import RequestBudget
    from app.planning.candidates import CandidateIndex, _prepare_geometry
    from app.routing.client import OpenRouteServiceAdapter
    from scripts.benchmarking.contracts import validate_verified_trip

    sample = json.loads(args.sample.read_text(encoding="utf-8"))
    sample_hash = hashlib.sha256(args.sample.read_bytes()).hexdigest()
    if sample["candidate_sha256"] != descriptor["dataset"]["candidate_sha256"]:
        raise ValueError("sample candidate identity differs from the release")
    identities = {
        "sample_sha256": sample_hash,
        "planner_sha256": planner_hash(ROOT),
        "candidate_sha256": sample["candidate_sha256"],
        "source_sha256": descriptor["dataset"]["source_sha256"],
        "road": descriptor["road"],
        "asset_sha256": descriptor["sha256"],
        "benchmark_source_sha256": benchmark_sources(ROOT),
    }
    if args.resume:
        report = json.loads(args.output.read_text(encoding="utf-8"))
        if any(report.get(key) != value for key, value in identities.items()):
            raise ValueError("resume identity differs from the saved run")
        if report.get("finished_utc"):
            raise ValueError("run already completed")
        report.setdefault("resumed_utc", []).append(datetime.now(UTC).isoformat())
    else:
        if args.output.exists():
            raise ValueError("output exists; use --resume or a new path")
        report = {
            "schema": 1,
            "created_utc": datetime.now(UTC).isoformat(),
            **identities,
            "release": manifest["version"],
            "road": descriptor["road"],
            "sample": sample,
            "environment": {
                "transport": "live Waitress HTTP on loopback",
                "routing": "live openrouteservice fastest driving-car",
                "platform": platform.platform(),
                "logical_processors": os.cpu_count(),
                "waitress_threads": 12,
                "planning_capacity": descriptor["hosted_settings"][
                    "FUEL_PLANNING_CAPACITY"
                ],
            },
            "protocol": {
                "selection": sample["selection"],
                "sample_cases": len(sample["cases"]),
                "sample_states": len(sample["states"]),
                "measurement": "One routing-cold HTTP request per fixed case; "
                "prepared assets stay loaded. Provider pacing and preflight "
                "are outside the timer.",
                "feasibility": "Preflight uses all-offer first-leg distance bounds "
                "or independent road itineraries with fuel range and snap checks; "
                "no production planner results.",
                "unresolved": "No witness is not evidence of infeasibility. "
                "Unresolved rejections stay separate.",
                "cost_and_concurrency": "The separate performance capture records "
                "its actual route subset, trials, cache states, independent cost "
                "and concurrency measurements.",
            },
            "feasibility": {},
            "rows": [],
        }
    index = load_candidates(
        descriptor["dataset"]["source_sha256"], sample["candidate_sha256"]
    )
    by_id = {station.station_id: station for station in index.stations}
    resolved = {}
    for name, case in sample["cases"].items():
        trip = TripInput(
            case["start"],
            case["finish"],
            Decimal(case["initial_fuel_gallons"]),
            Decimal("3.20"),
            Decimal(case["reserve_gallons"]),
            None,
            None,
            "balanced",
            Decimal(30),
        )
        start, finish = resolve_inputs(trip)
        resolved[name] = (start.coordinates, finish.coordinates)
    pacing = BenchmarkBudget(
        args.call_ledger, args.call_cap, stop_at=args.stop_at_calls
    )
    pacing.__enter__()
    atexit.register(pacing.__exit__)
    baseline_cache = {}
    adapter = OpenRouteServiceAdapter(os.environ["ORS_API_KEY"])
    try:
        for name, case in sample["cases"].items():
            if name in report["feasibility"]:
                continue
            result = preflight_case(
                case, *resolved[name], index.stations, adapter, baseline_cache, pacing
            )
            result["established_utc"] = datetime.now(UTC).isoformat()
            report["feasibility"][name] = result
            save(args.output, report)
            print(
                json.dumps(
                    {
                        "phase": "preflight",
                        "case": name,
                        "status": result["status"],
                        "completed": len(report["feasibility"]),
                    }
                ),
                flush=True,
            )
    finally:
        adapter.close()
    report.setdefault("preflight_finished_utc", datetime.now(UTC).isoformat())
    report.setdefault("measurements_started_utc", datetime.now(UTC).isoformat())
    save(args.output, report)
    contexts = threading.local()
    budgets = {}
    guard = threading.Lock()

    def observed_budget():
        budget = RequestBudget()
        with guard:
            budgets[contexts.identity] = budget
        return budget

    application = get_wsgi_application()

    def observed_application(environ, start_response):
        contexts.identity = environ.get("HTTP_X_BENCHMARK_ID", "health")
        return application(environ, start_response)

    channels = {}
    server = create_server(
        observed_application, host="127.0.0.1", port=0, threads=12, map=channels
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    def drain():
        end = time.monotonic() + 25
        while planning_activity()["active_planners"]:
            if time.monotonic() > end:
                raise RuntimeError("benchmark planners did not drain")
            time.sleep(0.05)

    try:
        with (
            patch("app.api.views.RequestBudget", observed_budget),
            httpx.Client(
                base_url=f"http://127.0.0.1:{server.effective_port}",
                timeout=22,
                trust_env=False,
            ) as client,
        ):
            if client.get("/health/ready").status_code != 200:
                raise RuntimeError("installed API is not ready")
            completed = {row["case"] for row in report["rows"]}
            for name, case in sample["cases"].items():
                if name in completed:
                    continue
                pacing.before_request(4)
                drain()
                trip_cache._entries.clear()
                verified_incumbent_cache._entries.clear()
                baseline_route_cache._entries.clear()
                _prepare_geometry.cache_clear()
                CandidateIndex.along_route.cache_clear()
                os.environ["FUEL_MATRIX_CACHE_VERSION"] = (
                    f"state-sample-{sample_hash[:12]}-{name}-{time.time_ns()}"
                )
                body = {
                    key: case[key]
                    for key in (
                        "start",
                        "finish",
                        "initial_fuel_gallons",
                        "reserve_gallons",
                    )
                }
                body.update(
                    initial_fuel_price_per_gallon="3.20",
                    optimization_mode="balanced",
                    max_extra_duration_minutes="30",
                )
                row = {
                    "case": name,
                    "cache_state": "routing_cold",
                    "contract_ok": False,
                }
                began = time.perf_counter()
                try:
                    response = client.post(
                        "/api/v1/trips", json=body, headers={"X-Benchmark-Id": name}
                    )
                    row["seconds"] = round(time.perf_counter() - began, 4)
                    payload = response.json()
                    row.update(
                        http_status=response.status_code,
                        error=payload.get("error", {}).get("code"),
                    )
                    if response.status_code == 200:
                        errors = validate_verified_trip(payload, set(by_id))
                        for stop in payload["fuel_stops"]:
                            if (
                                stop["station_id"] in by_id
                                and Decimal(stop["price_per_gallon"])
                                != by_id[stop["station_id"]].price_per_gallon
                            ):
                                errors.append("selected price differs from CSV")
                        saved = client.get(
                            f"/api/v1/trips/{payload['trip_id']}",
                            headers={
                                "Authorization": "Bearer " + payload["result_token"]
                            },
                        )
                        saved_ok = saved.status_code == 200 and saved.json() == {
                            key: value
                            for key, value in payload.items()
                            if key not in {"result_token", "map_url"}
                        }
                        row.update(
                            contract_ok=not errors and saved_ok,
                            contract_errors=errors,
                            saved_result_matches=saved_ok,
                            purchase_cost=payload["costs"]["fuel_purchases_cost"],
                            station_ids=[
                                stop["station_id"] for stop in payload["fuel_stops"]
                            ],
                            distance_miles=payload["route"]["distance_miles"],
                        )
                except (httpx.HTTPError, ValueError, KeyError) as error:
                    row.update(
                        seconds=round(time.perf_counter() - began, 4),
                        http_status=None,
                        error=type(error).__name__,
                    )
                budget = budgets.get(name)
                row.update(
                    hosted_attempts=budget.attempts if budget else 0,
                    stage_seconds=budget.stage_seconds if budget else {},
                )
                pacing.record(row["hosted_attempts"])
                row["outcome"] = classify(row, report["feasibility"][name])
                report["rows"].append(row)
                save(args.output, report)
                print(
                    json.dumps(
                        {
                            "phase": "http",
                            "case": name,
                            "outcome": row["outcome"],
                            "seconds": row["seconds"],
                            "completed": len(report["rows"]),
                        }
                    ),
                    flush=True,
                )
    finally:
        drain()
        shutdown_planning(timeout=20)
        server.task_dispatcher.shutdown(timeout=5)
        wasyncore.close_all(map=channels)
        thread.join(timeout=2)
    report["summary"] = outcome_summary(report["rows"], report["feasibility"])
    report["timing"] = http_summary(report["rows"])
    report["finished_utc"] = datetime.now(UTC).isoformat()
    save(args.output, report)
    print(json.dumps(report["summary"]), flush=True)


if __name__ == "__main__":
    main()
