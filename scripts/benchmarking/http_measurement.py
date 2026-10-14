"""Live HTTP timing, persistence verification and source-price checks."""

import time
from decimal import Decimal

import httpx

from scripts.benchmarking.cases import BENCHMARK_CASES
from scripts.benchmarking.contracts import validate_verified_trip


def measure_http(
    url, by_id, budgets, name, state, trial, phase, barrier=None, concurrency=1
):
    case = BENCHMARK_CASES[name]
    identity = f"{phase}/{concurrency}/{name}/{state}/{trial}"
    body = {
        "start": case.start,
        "finish": case.finish,
        "initial_fuel_gallons": case.initial,
        "reserve_gallons": case.reserve,
        "initial_fuel_price_per_gallon": "3.20",
        "optimization_mode": "balanced",
        "max_extra_duration_minutes": "30",
    }
    with httpx.Client(base_url=url, timeout=22, trust_env=False) as client:
        if barrier:
            barrier.wait(timeout=10)
        began = time.perf_counter()
        row = {
            "case": name,
            "cache_state": state,
            "trial": trial,
            "mode": "hosted_default",
            "phase": phase,
            "concurrency": concurrency,
            "contract_ok": False,
        }
        try:
            response = client.post(
                "/api/v1/trips", json=body, headers={"X-Benchmark-Id": identity}
            )
            row["seconds"] = round(time.perf_counter() - began, 4)
            payload = response.json()
            row.update(
                http_status=response.status_code,
                error=payload.get("error", {}).get("code"),
                retry_after=response.headers.get("Retry-After"),
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
                row.update(
                    contract_ok=not errors,
                    contract_errors=errors,
                    purchase_cost=payload["costs"]["fuel_purchases_cost"],
                    station_ids=[stop["station_id"] for stop in payload["fuel_stops"]],
                    routed_station_count=len(payload["route"]["legs"]) - 1,
                    distance_miles=payload["route"]["distance_miles"],
                    extra_minutes=str(
                        Decimal(payload["route"]["extra_duration_seconds"]) / 60
                    ),
                    profile="ors_fastest_driving_car",
                )
                miles = Decimal(row["distance_miles"])
                row["trip_class"] = (
                    "short" if miles <= 150 else "regional" if miles <= 600 else "long"
                )
                saved = client.get(
                    f"/api/v1/trips/{payload['trip_id']}",
                    headers={"Authorization": "Bearer " + payload["result_token"]},
                )
                row["saved_result_matches"] = (
                    saved.status_code == 200
                    and saved.json()
                    == {
                        key: value
                        for key, value in payload.items()
                        if key not in {"result_token", "map_url"}
                    }
                )
                row["contract_ok"] &= row["saved_result_matches"]
        except (httpx.HTTPError, ValueError, KeyError) as error:
            row.update(
                seconds=round(time.perf_counter() - began, 4),
                http_status=None,
                error=type(error).__name__,
            )
    budget = budgets.get(identity)
    row.update(
        hosted_attempts=budget.attempts if budget else 0,
        stage_seconds=budget.stage_seconds if budget else {},
        work_metrics=budget.metrics if budget else {},
        first_verified_seconds=budget.first_verified_seconds if budget else None,
    )
    return row
