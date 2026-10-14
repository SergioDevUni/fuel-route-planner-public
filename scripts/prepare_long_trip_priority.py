"""Prioritize CSV road sources near long-trip routes, then retain the US queue.

The corridor controls offline work order only, never the national search scope.
Local baseline geometry is not a verified fuel plan or a cheapest-plan claim.
"""

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from app.planning.budget import RequestBudget
from app.planning.candidates import CandidateIndex
from app.routing.local_valhalla import LocalValhalla
from scripts.benchmark_matrix_selector import CASES, TripInput, resolve_inputs

ROOT = Path(__file__).resolve().parents[1]
LONG_CASES = (
    "los_angeles_dallas",
    "seattle_denver",
    "chicago_miami",
    "new_york_los_angeles",
)
CORRIDOR_METERS = 80_467.2  # 50 miles either side; a scheduling heuristic.


def main() -> None:
    folder = ROOT / "var/station_candidates"
    manifest = json.loads((folder / "manifest.json").read_text())
    candidates = CandidateIndex(folder, manifest["source_sha256"])
    database = ROOT / "var/road_nationwide/station_edges.db"
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        if metadata["candidate_hash"] != candidates.version:
            raise ValueError("road cache differs from current station snapshot")
        by_coordinate = {
            (latitude, longitude): point_id
            for point_id, latitude, longitude in connection.execute(
                "SELECT point_id, latitude, longitude FROM point "
                "WHERE snap_meters IS NOT NULL"
            )
        }
        completed = {
            row[0] for row in connection.execute("SELECT point_id FROM complete_source")
        }
    router = LocalValhalla(
        "http://127.0.0.1:8004",
        metadata["road_graph_version"],
        request_timeout_seconds=60,
    )
    priority: set[int] = set()
    cases = []
    try:
        for name in LONG_CASES:
            start_spec, finish_spec, initial = CASES[name]
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
            route = router.route(
                (start.coordinates, finish.coordinates),
                RequestBudget(deadline_seconds=120),
            )
            offers = candidates.along_route(route, CORRIDOR_METERS)
            ids = {
                by_coordinate[key]
                for item in offers
                if (
                    key := (
                        item.station.coordinates.latitude,
                        item.station.coordinates.longitude,
                    )
                )
                in by_coordinate
            }
            priority.update(ids)
            record = {
                "case": name,
                "route_miles": float(route.distance_meters / Decimal("1609.344")),
                "csv_offers": len({item.station.station_id for item in offers}),
                "priority_points": len(ids),
                "already_complete": len(ids & completed),
                "point_ids": sorted(ids),
            }
            cases.append(record)
            print(
                json.dumps({k: v for k, v in record.items() if k != "point_ids"}),
                flush=True,
            )
    finally:
        router.close()
    document = {
        "candidate_version": candidates.version,
        "graph_version": metadata["road_graph_version"],
        "selection": (
            "50-mile corridor around local auto-profile baseline; work order only"
        ),
        "cases": cases,
        "point_ids": sorted(priority),
    }
    output = ROOT / "var/road_nationwide/priority_long_trips.json"
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "priority_points": len(priority),
                "already_complete": len(priority & completed),
                "remaining": len(priority - completed),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
