"""Compare fresh hosted and partial local-first trips at the real API boundary.

Provider and endpoint caches remain in their current state. Each request gets
a fresh result-cache entry; the report excludes credentials and result tokens.
"""

import argparse
import json
import logging
import os
from decimal import Decimal
from pathlib import Path
from time import perf_counter

from scripts.benchmarking.cases import CASES
from scripts.benchmarking.contracts import validate_verified_trip

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.test import Client  # noqa: E402

from app.api.assets import current_candidate_hash, load_candidates  # noqa: E402
from app.api.cache import trip_cache  # noqa: E402
from app.stations.models import DatasetVersion  # noqa: E402

# noqa: E402
from scripts.check_pilot_routes import routing_key  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def measure(case: str, mode: str, csv_ids: set[str]) -> dict[str, object]:
    start, finish, initial = CASES[case]
    os.environ["FUEL_PARTIAL_ROAD_REFINER"] = "1" if mode != "hosted_default" else "0"
    os.environ["FUEL_PARTIAL_ROAD_LOCAL_FIRST"] = "1" if mode == "local_first" else "0"
    os.environ["FUEL_PARTIAL_ROAD_PORTFOLIO"] = "1" if mode == "portfolio" else "0"
    trip_cache._entries.clear()
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
        "/api/v1/trips", data=json.dumps(request), content_type="application/json"
    )
    elapsed = round(perf_counter() - started, 3)
    payload = response.json()
    report: dict[str, object] = {
        "case": case,
        "mode": mode,
        "http_status": response.status_code,
        "seconds": elapsed,
        "under_15_seconds": elapsed <= 15,
        "error": payload.get("error", {}).get("code"),
    }
    if response.status_code == 200:
        route = payload["route"]
        contract_errors = validate_verified_trip(payload, csv_ids)
        report.update(
            {
                "contract_ok": not contract_errors,
                "contract_errors": contract_errors,
                "selection": payload["assumptions"]["station_selection"],
                "station_ids": [stop["station_id"] for stop in payload["fuel_stops"]],
                "purchase_cost": payload["costs"]["fuel_purchases_cost"],
                "extra_minutes": str(
                    round(Decimal(route["extra_duration_seconds"]) / 60, 3)
                ),
                "within_30_extra_minutes": (
                    Decimal(route["extra_duration_seconds"]) <= 1800
                ),
            }
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="+", choices=tuple(CASES))
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("hosted_default", "local_first", "portfolio"),
        default=("hosted_default", "local_first", "portfolio"),
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--url",
        default=os.environ.get("FUEL_NATIONAL_VALHALLA_URL", "http://127.0.0.1:8005"),
    )
    parser.add_argument(
        "--graph-version",
        default=os.environ.get(
            "FUEL_NATIONAL_VALHALLA_VERSION",
            "geofabrik-us-20261009-valhalla-3.9.1-auto",
        ),
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if args.verbose:
        for name in (
            "app.planning.portfolio",
            "app.planning.national_refiner",
            "app.planning.trip",
        ):
            logging.getLogger(name).setLevel(logging.INFO)
            logging.getLogger(name).addHandler(logging.StreamHandler())
    os.environ["ORS_API_KEY"] = routing_key()
    active = DatasetVersion.objects.filter(status="active").first()
    if active is None:
        parser.error("station dataset is not active")
    csv_ids = {
        station.station_id
        for station in load_candidates(
            active.source_sha256, current_candidate_hash()
        ).stations
    }
    os.environ["FUEL_NATIONAL_VALHALLA_URL"] = args.url
    os.environ["FUEL_NATIONAL_VALHALLA_VERSION"] = args.graph_version
    lines = []
    for case in args.cases:
        for mode in args.modes:
            for trial in range(1, args.repeat + 1):
                result = measure(case, mode, csv_ids)
                result["trial"] = trial
                print(json.dumps(result, sort_keys=True), flush=True)
                lines.append(result)
    if args.report:
        report = args.report if args.report.is_absolute() else ROOT / args.report
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            "\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
