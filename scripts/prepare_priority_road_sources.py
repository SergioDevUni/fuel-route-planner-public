"""Select versioned road-source IDs for a measured long-route CSV case."""

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
from app.planning.candidates import CandidateIndex  # noqa: E402
from app.planning.national_scope import screen_csv_offers  # noqa: E402
from app.stations.models import DatasetVersion  # noqa: E402
from scripts.benchmark_matrix_selector import CASES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK = ROOT / "var/benchmarks/2026-10-10-predeadline-matrix.jsonl"
DEFAULT_DATABASE = ROOT / "var/road_nationwide/station_edges.db"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, default="albuquerque_denver")
    parser.add_argument("--selected-only", action="store_true")
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    measured = [
        json.loads(line)
        for line in args.benchmark.read_text(encoding="utf-8").splitlines()
    ]
    row = next((item for item in measured if item.get("case") == args.case), None)
    if row is None or row.get("status") != "verified":
        raise ValueError("priority case needs a verified benchmark incumbent")
    start_spec, finish_spec, initial_text = CASES[args.case]
    initial = Decimal(initial_text)
    trip = TripInput(
        start_spec,
        finish_spec,
        initial,
        Decimal("3.20"),
        Decimal(0),
        None,
        None,
        "balanced",
        Decimal(30),
    )
    start, finish = resolve_inputs(trip)
    active = DatasetVersion.objects.get(status="active")
    candidates = CandidateIndex(ROOT / "var/station_candidates", active.source_sha256)
    scope = screen_csv_offers(
        candidates.stations,
        start.coordinates,
        finish.coordinates,
        initial,
        Decimal(0),
        Decimal(row["purchase_cost"]),
    )
    with sqlite3.connect(args.database) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        if metadata.get("candidate_hash") != candidates.version:
            raise ValueError("road cache does not match active candidate snapshot")
        by_coordinate = {
            (latitude, longitude): point_id
            for point_id, latitude, longitude in connection.execute(
                "SELECT point_id, latitude, longitude FROM point"
            )
        }
        completed = {
            point_id
            for (point_id,) in connection.execute(
                "SELECT point_id FROM complete_source"
            )
        }
    selected_stations = (
        tuple(
            next(
                item
                for item in candidates.stations
                if item.station_id == stop["station_id"]
            )
            for stop in row["selected_stops"]
        )
        if args.selected_only
        else tuple(item.station for item in scope.possible_offers)
    )
    point_ids = sorted(
        {
            by_coordinate[(station.coordinates.latitude, station.coordinates.longitude)]
            for station in selected_stations
        }
    )
    output = args.output or (
        ROOT
        / "var/road_nationwide"
        / f"priority_{args.case}{'_seed' if args.selected_only else ''}.json"
    )
    document = {
        "case": args.case,
        "candidate_version": candidates.version,
        "graph_version": metadata["road_graph_version"],
        "incumbent_purchase_cost": row["purchase_cost"],
        "screened_csv_offers": len(scope.possible_offers),
        "selected_only": args.selected_only,
        "point_ids": point_ids,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "case": args.case,
                "screened_csv_offers": len(scope.possible_offers),
                "priority_points": len(point_ids),
                "already_complete": len(set(point_ids) & completed),
                "remaining": len(set(point_ids) - completed),
                "output": str(output),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
