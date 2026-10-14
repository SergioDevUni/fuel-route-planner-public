"""Prepare missing road sources that can still improve verified CSV trips."""

import argparse
import json
import os
import sqlite3
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from app.api.inputs import resolve_inputs  # noqa: E402
from app.api.validation import TripInput  # noqa: E402
from app.domain.routing import Coordinates  # noqa: E402
from app.planning.candidates import CandidateIndex  # noqa: E402
from app.planning.national_graph import completed_road_points  # noqa: E402
from app.planning.national_scope import screen_csv_offers  # noqa: E402
from app.stations.models import DatasetVersion  # noqa: E402
from scripts.benchmarking.cases import CASES  # noqa: E402
from scripts.check_csv_trip_costs import _incumbents  # noqa: E402
from scripts.simulate_csv_cost_review import (  # noqa: E402
    DATABASE,
    DEFAULT_REPORTS,
    GRAPH_VERSION,
    ROOT,
)

LONG_CASES = (
    "los_angeles_dallas",
    "seattle_denver",
    "chicago_miami",
    "new_york_los_angeles",
)
DEFAULT_OUTPUT = ROOT / "var/road_nationwide/priority_cost_review_remaining.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", default=LONG_CASES)
    parser.add_argument("--reports", nargs="+", type=Path, default=DEFAULT_REPORTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    active = DatasetVersion.objects.get(status="active")
    index = CandidateIndex(ROOT / "var/station_candidates", active.source_sha256)
    completed = completed_road_points(
        DATABASE, index.version, GRAPH_VERSION, active_point_hash=index.road_point_hash
    )
    with sqlite3.connect(f"file:{DATABASE.as_posix()}?mode=ro", uri=True) as db:
        point_ids = {
            Coordinates(latitude, longitude): point_id
            for point_id, latitude, longitude in db.execute(
                "SELECT point_id, latitude, longitude FROM point WHERE active = 1"
            )
        }
    incumbents = _incumbents(tuple(args.reports))
    priority: set[int] = set()
    cases: list[dict] = []
    for case in args.cases:
        start_spec, finish_spec, initial = CASES[case]
        trip = TripInput(
            start_spec,
            finish_spec,
            Decimal(initial),
            Decimal("3.20"),
            Decimal(0),
            None,
            None,
            "balanced",
            Decimal(30),
        )
        start, finish = resolve_inputs(trip)
        incumbent_cost = Decimal(incumbents[case]["purchase_cost"])
        baseline_scope = screen_csv_offers(
            index.stations,
            start.coordinates,
            finish.coordinates,
            trip.initial_fuel_gallons,
            trip.reserve_gallons,
            incumbent_cost,
        )
        scope = screen_csv_offers(
            index.stations,
            start.coordinates,
            finish.coordinates,
            trip.initial_fuel_gallons,
            trip.reserve_gallons,
            incumbent_cost,
            price_access_bound=True,
        )
        offers = scope.possible_offers
        record = {
            "case": case,
            "incumbent_purchase_cost": incumbents[case]["purchase_cost"],
            "baseline_cost_screen_survivors": len(baseline_scope.possible_offers),
            "cost_screen_survivors": len(offers),
        }
        missing_points = {
            item.station.coordinates
            for item in offers
            if item.station.coordinates not in completed
        }
        missing_ids = {point_ids[point] for point in missing_points}
        priority.update(missing_ids)
        record["remaining_offers"] = len(offers)
        record["missing_source_points"] = len(missing_ids)
        cases.append(record)
    output = {
        "candidate_version": index.version,
        "graph_version": GRAPH_VERSION,
        "selection": "safe CSV cost and cheaper-fuel access bounds",
        "completed_source_points": len(completed),
        "cases": cases,
        "point_ids": sorted(priority),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in output.items() if key != "point_ids"},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
