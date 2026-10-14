"""Audit whether a selected station matrix can assess cheaper-site exclusion.

The bound uses geodesic distance and the cheapest CSV gallon anywhere. It is
safe but weak: unresolved sites are not evidence that a cheaper route exists.
No hosted routing calls are made.
"""

import argparse
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from app.api.inputs import resolve_inputs  # noqa: E402
from app.api.validation import TripInput  # noqa: E402
from app.domain.routing import Route, RouteLeg, RouteWaypoint  # noqa: E402
from app.planning.candidates import CandidateIndex  # noqa: E402
from app.planning.matrix_candidates import select_matrix_candidates  # noqa: E402
from app.planning.national_scope import screen_csv_offers  # noqa: E402
from app.routing.geometry import geodesic_meters  # noqa: E402
from app.stations.models import DatasetVersion  # noqa: E402
from scripts.benchmark_matrix_selector import CASES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def audit(case: str, incumbent: Decimal) -> dict:
    start_spec, finish_spec, initial = CASES[case]
    request = TripInput(
        start_spec,
        finish_spec,
        Decimal(initial),
        Decimal("3.20"),
        Decimal(0),
        None,
        None,
    )
    start, finish = resolve_inputs(request)
    candidates = CandidateIndex(
        ROOT / "var/station_candidates",
        DatasetVersion.objects.get(status="active").source_sha256,
    )
    chord = geodesic_meters(start.coordinates, finish.coordinates)
    projection = Route(
        (start.coordinates, finish.coordinates),
        (
            RouteWaypoint(start.coordinates, start.coordinates, 0, Decimal(0)),
            RouteWaypoint(finish.coordinates, finish.coordinates, 1, Decimal(0)),
        ),
        (RouteLeg(chord, Decimal(0)),),
        chord,
        Decimal(0),
        "scope-audit-chord",
        datetime.now(UTC),
    )
    along = candidates.along_route(projection, 80_000)
    nearby = {item.station.station_id for item in along}
    envelope = candidates.within_detour_envelope(projection, chord * 2)
    selected = (
        select_matrix_candidates(
            (),
            along
            + tuple(item for item in envelope if item.station.station_id not in nearby),
            chord,
            offset_price_penalty_per_mile=Decimal("0.02"),
        )
        or ()
    )
    chosen_ids = {item.station.station_id for item in selected}
    scope = screen_csv_offers(
        candidates.stations,
        start.coordinates,
        finish.coordinates,
        request.initial_fuel_gallons,
        request.reserve_gallons,
        incumbent,
    )
    unresolved = [
        (item.minimum_purchase_cost, item.station.station_id)
        for item in scope.possible_offers
        if item.station.station_id not in chosen_ids
    ]
    excluded = scope.excluded_by_cost
    lowest_price = min(item.price_per_gallon for item in candidates.stations)
    return {
        "case": case,
        "incumbent_cost": str(incumbent),
        "cheapest_csv_price": str(lowest_price),
        "eligible_us_offers": len(candidates.stations),
        "selected_offers": len(chosen_ids),
        "omitted_excluded_by_safe_bound": excluded,
        "omitted_unresolved": len(unresolved),
        "sample_unresolved_ids": [item[1] for item in sorted(unresolved)[:8]],
        "all_candidate_offers_accounted": not unresolved,
        "road_path_checks_completed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=CASES)
    parser.add_argument("incumbent_cost", type=Decimal)
    args = parser.parse_args()
    print(json.dumps(audit(args.case, args.incumbent_cost)))


if __name__ == "__main__":
    main()
