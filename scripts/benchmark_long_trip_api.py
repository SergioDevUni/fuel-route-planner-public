"""Run three regional long-trip requests through the real trip API boundary.

The report excludes routing credentials, result tokens, and full route geometry.
Cases are run sequentially so their planner times are directly comparable.
"""

import json
import os
from decimal import Decimal
from pathlib import Path
from time import perf_counter

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402

from scripts.benchmark_matrix_selector import CASES  # noqa: E402
from scripts.check_pilot_routes import routing_key  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SELECTED_CASES = (
    "los_angeles_dallas",
    "seattle_denver",
    "chicago_miami",
)


def run_case(name: str) -> dict[str, object]:
    start, finish, initial = CASES[name]
    request = {
        "start": start,
        "finish": finish,
        "initial_fuel_gallons": initial,
        "initial_fuel_price_per_gallon": "3.20",
        "optimization_mode": "balanced",
        "max_extra_duration_minutes": "30",
    }
    started = perf_counter()
    response = Client().post(
        "/api/v1/trips",
        data=json.dumps(request),
        content_type="application/json",
    )
    elapsed = round(perf_counter() - started, 3)
    data = response.json()
    result: dict[str, object] = {
        "case": name,
        "http_status": response.status_code,
        "elapsed_seconds": elapsed,
        "trip_status": data.get("status"),
        "error": data.get("error", {}).get("code"),
    }
    if response.status_code == 200:
        route = data["route"]
        stops = data["fuel_stops"]
        result.update(
            {
                "selection": data["assumptions"]["station_selection"],
                "baseline_miles": route["baseline_distance_miles"],
                "route_miles": route["distance_miles"],
                "extra_minutes": str(
                    round(Decimal(route["extra_duration_seconds"]) / 60, 3)
                ),
                "paid_station_ids": [stop["station_id"] for stop in stops],
                "purchase_cost_usd": data["costs"]["fuel_purchases_cost"],
                "within_15_seconds": elapsed <= 15,
                "within_30_extra_minutes": (
                    Decimal(route["extra_duration_seconds"]) <= 1800
                ),
            }
        )
        retrieved = Client().get(
            f"/api/v1/trips/{data['trip_id']}",
            HTTP_AUTHORIZATION=f"Bearer {data['result_token']}",
        )
        result["saved_retrieval_http_status"] = retrieved.status_code
    return result


def main() -> None:
    os.environ["ORS_API_KEY"] = routing_key()
    report = ROOT / "var/benchmarks/2026-10-10-long-trip-api.jsonl"
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w", encoding="utf-8") as output:
        for name in SELECTED_CASES:
            result = run_case(name)
            line = json.dumps(result, sort_keys=True)
            print(line, flush=True)
            output.write(line + "\n")
            output.flush()


if __name__ == "__main__":
    main()
