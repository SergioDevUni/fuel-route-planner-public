"""Compare ORS fastest and shortest final routes through chosen CSV stops."""

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from app.api.inputs import resolve_inputs  # noqa: E402
from app.api.validation import TripInput  # noqa: E402
from app.domain.fuel import FuelOpportunity  # noqa: E402
from app.domain.units import METERS_PER_MILE  # noqa: E402
from app.planning.budget import RequestBudget  # noqa: E402
from app.planning.candidates import CandidateIndex  # noqa: E402
from app.planning.fuel import optimize_fuel  # noqa: E402
from app.routing.client import DIRECTIONS_URL, OpenRouteServiceAdapter  # noqa: E402
from app.routing.normalizer import normalize_ors_geojson  # noqa: E402
from app.stations.models import DatasetVersion  # noqa: E402
from scripts.benchmark_matrix_selector import CASES  # noqa: E402
from scripts.check_pilot_routes import routing_key  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STOPS = {
    "chicago_st_louis": ("69895", "67008"),
    "green_river_price": ("70793",),
    "albuquerque_denver": ("7228", "469", "3817"),
}


def study(case_name: str) -> dict:
    start_spec, finish_spec, initial = CASES[case_name]
    request = TripInput(
        start_spec,
        finish_spec,
        Decimal(initial),
        Decimal("3.20"),
        Decimal(0),
        Decimal(20),
        Decimal("0.1"),
    )
    start, finish = resolve_inputs(request)
    active = DatasetVersion.objects.get(status="active")
    stations = CandidateIndex(
        ROOT / "var/station_candidates", active.source_sha256
    ).stations
    by_id = {station.station_id: station for station in stations}
    selected = tuple(by_id[station_id] for station_id in STOPS[case_name])
    points = (
        start.coordinates,
        *(station.coordinates for station in selected),
        finish.coordinates,
    )
    adapter = OpenRouteServiceAdapter(routing_key())
    budget = RequestBudget(deadline_seconds=45)
    try:
        results = []
        for preference in ("fastest", "shortest"):
            payload = adapter._post(
                DIRECTIONS_URL,
                {
                    "coordinates": [list(point.as_provider_pair()) for point in points],
                    "preference": preference,
                },
                budget,
            )
            route = normalize_ors_geojson(payload, points)
            fuel = optimize_fuel(
                tuple(
                    FuelOpportunity(station.station_id, station.price_per_gallon)
                    for station in selected
                ),
                tuple(leg.distance_meters for leg in route.legs),
                request.initial_fuel_gallons,
            )
            results.append(
                {
                    "preference": preference,
                    "miles": round(float(route.distance_meters / METERS_PER_MILE), 3),
                    "minutes": round(float(route.duration_seconds / 60), 1),
                    "purchase_cost": str(fuel.exact_purchase_cost),
                    "paid_ids": [
                        visit.station_id
                        for visit in fuel.visits
                        if visit.purchased_gallons > 0
                    ],
                    "stop_snap_meters": [
                        str(waypoint.snap_distance_meters)
                        for waypoint in route.waypoints[1:-1]
                    ],
                }
            )
        return {
            "case": case_name,
            "station_ids": STOPS[case_name],
            "precisions": [station.location_precision for station in selected],
            "hosted_attempts": budget.attempts,
            "routes": results,
        }
    finally:
        adapter.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=STOPS)
    args = parser.parse_args()
    print(json.dumps(study(args.case), indent=2))


if __name__ == "__main__":
    main()
