"""Compare live corridor and matrix planning on fixed trip cases.

Each mode makes a fresh plan and uses its own four-attempt routing budget.
The default policy allows 30 extra driving minutes with no distance cap.
The script prints summary facts only and never writes the routing credential.
"""

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path
from time import perf_counter

from scripts.benchmarking.cases import BASE_CASES as CASES

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from app.api.inputs import resolve_inputs  # noqa: E402
from app.api.routing_config import regional_config  # noqa: E402
from app.api.validation import TripInput  # noqa: E402
from app.domain.units import METERS_PER_MILE  # noqa: E402
from app.planning.baseline_cache import (  # noqa: E402
    BaselineRouteCache,
    CachedBaselineRouter,
)
from app.planning.candidates import CandidateIndex  # noqa: E402
from app.planning.matrix_cache import CachedRoadMatrixProvider  # noqa: E402
from app.planning.trip import TripPlanner  # noqa: E402
from app.routing.client import OpenRouteServiceAdapter  # noqa: E402
from app.routing.local_osrm import LocalOSRM  # noqa: E402
from app.routing.regional_matrix import RegionalRoadMatrixProvider  # noqa: E402
from app.stations.models import DatasetVersion  # noqa: E402
from scripts.check_pilot_routes import routing_key  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASELINE_CACHE = BaselineRouteCache()


def measure(
    case_name: str,
    mode: str,
    index: CandidateIndex,
    key: str,
    *,
    use_cache: bool = False,
    use_baseline_cache: bool = False,
    max_detour_miles: Decimal | None = None,
    max_detour_ratio: Decimal | None = None,
    max_extra_duration_minutes: Decimal = Decimal(30),
    use_regional_osrm: bool = False,
    matrix_first: bool = False,
) -> dict:
    start_spec, finish_spec, initial = CASES[case_name]
    request = TripInput(
        start_spec,
        finish_spec,
        Decimal(initial),
        Decimal("3.20"),
        Decimal(0),
        max_detour_miles,
        max_detour_ratio,
        "balanced",
        max_extra_duration_minutes,
    )
    start, finish = resolve_inputs(request)
    adapter = OpenRouteServiceAdapter(key)
    router = (
        CachedBaselineRouter(adapter, BASELINE_CACHE, "ors-fastest-driving-car:v1")
        if use_baseline_cache
        else adapter
    )
    matrix_provider = None
    local = None
    if mode == "matrix":
        matrix_provider = (
            CachedRoadMatrixProvider(adapter, ROOT / "var/matrix_cache")
            if use_cache
            else adapter
        )
        if use_regional_osrm:
            config = regional_config()
            if config is None:
                raise ValueError("regional OSRM settings are required")
            url, version, bounds = config
            local = LocalOSRM(url, version)
            matrix_provider = RegionalRoadMatrixProvider(local, matrix_provider, bounds)
    started = perf_counter()
    try:
        trip = TripPlanner(
            router,
            index,
            matrix_provider=matrix_provider,
        ).plan(
            start.coordinates,
            finish.coordinates,
            request.initial_fuel_gallons,
            request.reserve_gallons,
            request.max_detour_miles,
            request.max_detour_ratio,
            request.initial_fuel_price_per_gallon,
            request.optimization_mode,
            request.max_extra_duration_minutes,
            matrix_first=matrix_first,
        )
    except Exception as error:
        return {
            "case": case_name,
            "mode": mode,
            "status": type(error).__name__,
            "detail": str(error),
            "elapsed_seconds": round(perf_counter() - started, 3),
        }
    finally:
        adapter.close()
        if local is not None:
            local.close()
    paid_ids = [
        station.station_id
        for station, visit in zip(trip.routed_stations, trip.fuel.visits, strict=True)
        if visit.purchased_gallons > 0
    ]
    selected_stops = [
        {
            "station_id": station.station_id,
            "name": station.name,
            "address": station.address,
            "city": station.city,
            "state": station.state,
            "precision": station.location_precision,
            "snap_meters": round(
                float(trip.verified.waypoints[index + 1].snap_distance_meters), 3
            ),
            "purchased_gallons": round(float(visit.purchased_gallons), 6),
        }
        for index, (station, visit) in enumerate(
            zip(trip.routed_stations, trip.fuel.visits, strict=True)
        )
    ]
    return {
        "case": case_name,
        "mode": mode,
        "status": "verified",
        "selection": trip.selection_method,
        "paid_station_ids": paid_ids,
        "purchase_cost": str(trip.ledger.exact_purchase_cost),
        "baseline_minutes": round(float(trip.baseline.duration_seconds / 60), 2),
        "route_minutes": round(float(trip.verified.duration_seconds / 60), 2),
        "extra_minutes": round(
            float(
                (trip.verified.duration_seconds - trip.baseline.duration_seconds) / 60
            ),
            2,
        ),
        "selected_stops": selected_stops,
        "route_miles": round(float(trip.verified.distance_meters / METERS_PER_MILE), 3),
        "extra_miles": round(
            float(
                (trip.verified.distance_meters - trip.baseline.distance_meters)
                / METERS_PER_MILE
            ),
            3,
        ),
        "hosted_attempts": trip.outbound_attempts,
        "elapsed_seconds": round(perf_counter() - started, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="*", choices=CASES)
    parser.add_argument(
        "--mode", choices=("corridor", "matrix", "both"), default="both"
    )
    parser.add_argument("--matrix-cache", action="store_true")
    parser.add_argument("--baseline-cache", action="store_true")
    parser.add_argument("--max-detour-miles", type=Decimal)
    parser.add_argument("--max-detour-ratio", type=Decimal)
    parser.add_argument("--max-extra-minutes", type=Decimal, default=Decimal(30))
    parser.add_argument("--regional-osrm", action="store_true")
    parser.add_argument("--matrix-first", action="store_true")
    parser.add_argument("--report", type=Path, help="save JSONL results locally")
    args = parser.parse_args()
    if args.max_detour_miles is not None and not Decimal(
        0
    ) <= args.max_detour_miles <= Decimal(100):
        parser.error("max-detour-miles must be between 0 and 100")
    if args.max_detour_ratio is not None and not Decimal(
        0
    ) <= args.max_detour_ratio <= Decimal("0.5"):
        parser.error("max-detour-ratio must be between 0 and 0.5")
    if not Decimal(0) <= args.max_extra_minutes <= Decimal(30):
        parser.error("max-extra-minutes must be between 0 and 30")
    active = DatasetVersion.objects.get(status="active")
    index = CandidateIndex(ROOT / "var/station_candidates", active.source_sha256)
    key = routing_key()
    modes = ("corridor", "matrix") if args.mode == "both" else (args.mode,)
    results = []
    for case in args.cases or CASES:
        for mode in modes:
            result = measure(
                case,
                mode,
                index,
                key,
                use_cache=args.matrix_cache,
                use_baseline_cache=args.baseline_cache,
                max_detour_miles=args.max_detour_miles,
                max_detour_ratio=args.max_detour_ratio,
                max_extra_duration_minutes=args.max_extra_minutes,
                use_regional_osrm=args.regional_osrm,
                matrix_first=args.matrix_first and mode == "matrix",
            )
            results.append(result)
            print(json.dumps(result), flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            "".join(json.dumps(result) + "\n" for result in results),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
