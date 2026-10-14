"""Capture patched HTTP results and independent California first-stop evidence."""

import argparse
import hashlib
import json
import os
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import httpx

from scripts.feasibility_evidence import GEOD
from scripts.local_common import ROOT, load_local_env, planner_hash, verify_installed
from scripts.performance_metrics import http_summary

CASES = (
    "co_regional_20",
    "co_regional_50",
    "mt_regional_20",
    "mt_regional_50",
    "oh_long_20",
    "ca_regional_20",
    "ca_long_20",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
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
    from app.domain.units import METERS_PER_MILE
    from app.planning.baseline_cache import baseline_route_cache
    from app.planning.budget import RequestBudget
    from app.planning.candidates import CandidateIndex, _prepare_geometry
    from app.routing.client import OpenRouteServiceAdapter
    from scripts.benchmarking.contracts import validate_verified_trip
    from scripts.benchmarking.measurement import ProviderPacing

    source = (
        ROOT / "var/private-development/records/docs/evidence/nationwide-delivery.json"
    )
    original = json.loads(source.read_text(encoding="utf-8"))
    inputs = {name: original["sample"]["cases"][name] for name in CASES}
    index = load_candidates(
        descriptor["dataset"]["source_sha256"],
        descriptor["dataset"]["candidate_sha256"],
    )
    by_id = {station.station_id: station for station in index.stations}
    report = {
        "schema": 1,
        "created_utc": datetime.now(UTC).isoformat(),
        "release": manifest["version"],
        "planner_sha256": planner_hash(ROOT),
        "asset_sha256": descriptor["sha256"],
        "road": descriptor["road"],
        "candidate_sha256": index.version,
        "original_evidence_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "verification_script_sha256": hashlib.sha256(
            Path(__file__).read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest(),
        "inputs": inputs,
        "protocol": {
            "http": "Two routing-cold live Waitress HTTP trials per original input. "
            "Prepared assets stay loaded. Saved-result checks follow the POST timer.",
            "radii_meters": {"endpoints": 1000, "stations": 250},
            "fuel_rounding_tolerance_gallons": "1e-18",
            "first_stops": "Check every CSV offer within the conservative "
            "first-leg geodesic bound. Measure the fastest-profile first legs "
            "and route through each reachable first stop. "
            "Preserve the original 290-case capture.",
        },
        "first_stop_evidence": {},
        "rows": [],
    }

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.output)

    pacing = ProviderPacing()
    adapter = OpenRouteServiceAdapter(os.environ["ORS_API_KEY"])
    try:
        for name in CASES[-2:]:
            case = inputs[name]
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
            usable = (
                (trip.initial_fuel_gallons - trip.reserve_gallons)
                * 10
                * METERS_PER_MILE
            )
            possible = tuple(
                station
                for station in index.stations
                if GEOD.inv(
                    *start.coordinates.as_provider_pair(),
                    *station.coordinates.as_provider_pair(),
                )[2]
                <= float(usable) + 1350
            )
            pacing.before_request(4)
            budget = RequestBudget(deadline_seconds=30)
            try:
                matrix = adapter.matrix(
                    (
                        start.coordinates,
                        *(station.coordinates for station in possible),
                        finish.coordinates,
                    ),
                    budget,
                )
                baseline = adapter.route(
                    (start.coordinates, finish.coordinates), budget
                )
                results = []
                for position, station in enumerate(possible, 1):
                    first = matrix.distances_meters[0][position]
                    item = {
                        "station_id": station.station_id,
                        "name": station.name,
                        "first_leg_matrix_miles": str(first / METERS_PER_MILE)
                        if first is not None
                        else None,
                        "within_starting_range": first is not None and first <= usable,
                    }
                    results.append(item)
                    if item["within_starting_range"]:
                        route = adapter.route(
                            (
                                start.coordinates,
                                station.coordinates,
                                finish.coordinates,
                            ),
                            budget,
                        )
                        item.update(
                            first_leg_verified_miles=str(
                                route.legs[0].distance_meters / METERS_PER_MILE
                            ),
                            extra_minutes=str(
                                (route.duration_seconds - baseline.duration_seconds)
                                / 60
                            ),
                            station_snap_meters=str(
                                route.waypoints[1].snap_distance_meters
                            ),
                            endpoint_snap_meters=[
                                str(route.waypoints[0].snap_distance_meters),
                                str(route.waypoints[-1].snap_distance_meters),
                            ],
                        )
                eligible = [item for item in results if item["within_starting_range"]]
                supported = bool(eligible) and all(
                    Decimal(item["extra_minutes"]) > 30
                    and Decimal(item["first_leg_verified_miles"])
                    <= usable / METERS_PER_MILE
                    and Decimal(item["station_snap_meters"]) <= 250
                    for item in eligible
                )
                report["first_stop_evidence"][name] = {
                    "offers_checked": len(index.stations),
                    "possible_first_stops": len(possible),
                    "usable_range_miles": str(usable / METERS_PER_MILE),
                    "baseline_seconds": str(baseline.duration_seconds),
                    "engine_version": baseline.engine_version,
                    "fetched_at": baseline.fetched_at.isoformat(),
                    "candidates": results,
                    "supports_profile_infeasibility": supported,
                    "scope": "Measured fastest-driving profile and prepared CSV "
                    "station coordinates; two reachable first stops "
                    "exceed the driving allowance.",
                }
                save()
            finally:
                pacing.record(budget.attempts)
    finally:
        adapter.close()

    context = threading.local()
    budgets = {}

    def observed_budget():
        budget = RequestBudget()
        budgets[context.identity] = budget
        return budget

    application = get_wsgi_application()

    def observed_application(environ, start_response):
        context.identity = environ.get("HTTP_X_BENCHMARK_ID", "health")
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
                raise RuntimeError("verification workers did not drain")
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
            for trial in (1, 2):
                for name, case in inputs.items():
                    pacing.before_request(4)
                    drain()
                    trip_cache._entries.clear()
                    verified_incumbent_cache._entries.clear()
                    baseline_route_cache._entries.clear()
                    _prepare_geometry.cache_clear()
                    CandidateIndex.along_route.cache_clear()
                    identity = f"{name}/{trial}"
                    os.environ["FUEL_MATRIX_CACHE_VERSION"] = (
                        f"seven-fixes-{time.time_ns()}"
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
                        "trial": trial,
                        "cache_state": "routing_cold",
                        "contract_ok": False,
                    }
                    began = time.perf_counter()
                    response = client.post(
                        "/api/v1/trips", json=body, headers={"X-Benchmark-Id": identity}
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
                        matched = saved.status_code == 200 and saved.json() == {
                            key: value
                            for key, value in payload.items()
                            if key not in {"result_token", "map_url"}
                        }
                        row.update(
                            contract_ok=not errors and matched,
                            contract_errors=errors,
                            saved_result_matches=matched,
                            purchase_cost=payload["costs"]["fuel_purchases_cost"],
                            station_ids=[
                                stop["station_id"] for stop in payload["fuel_stops"]
                            ],
                            distance_miles=payload["route"]["distance_miles"],
                        )
                    budget = budgets.get(identity)
                    row.update(
                        hosted_attempts=budget.attempts if budget else 0,
                        stage_seconds=budget.stage_seconds if budget else {},
                    )
                    pacing.record(row["hosted_attempts"])
                    supported = (
                        name in report["first_stop_evidence"]
                        and report["first_stop_evidence"][name][
                            "supports_profile_infeasibility"
                        ]
                    )
                    row["supported_infeasible_rejection"] = (
                        supported
                        and response.status_code == 422
                        and row["error"] == "NO_FEASIBLE_PLAN_IN_SEARCH_SCOPE"
                    )
                    row["expected_outcome_ok"] = (
                        row["supported_infeasible_rejection"]
                        if name in CASES[-2:]
                        else row["contract_ok"]
                    )
                    report["rows"].append(row)
                    save()
                    print(
                        json.dumps(
                            {
                                "case": name,
                                "trial": trial,
                                "status": response.status_code,
                                "expected_outcome_ok": row["expected_outcome_ok"],
                                "seconds": row["seconds"],
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
    report["plan_metrics"] = http_summary(report["rows"])
    report["expected_outcomes"] = {
        "requests": len(report["rows"]),
        "matched": sum(row["expected_outcome_ok"] for row in report["rows"]),
        "verified_plans": sum(row["contract_ok"] for row in report["rows"]),
        "supported_infeasible_rejections": sum(
            row["supported_infeasible_rejection"] for row in report["rows"]
        ),
    }
    report["finished_utc"] = datetime.now(UTC).isoformat()
    save()
    print(json.dumps(report["expected_outcomes"]), flush=True)


if __name__ == "__main__":
    main()
