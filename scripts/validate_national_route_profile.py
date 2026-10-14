"""Compare the prepared US Valhalla auto profile with hosted ORS directions.

These are read-only routing probes. They do not claim nationwide station-search
coverage, and the result contains no routing credential.
"""

import argparse
import json
from pathlib import Path
from time import perf_counter

from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.routing.client import OpenRouteServiceAdapter
from app.routing.local_valhalla import LocalValhalla
from scripts.check_pilot_routes import routing_key

CASES = {
    "chicago_st_louis": ((41.8781, -87.6298), (38.6270, -90.1994)),
    "albuquerque_denver": ((35.0844, -106.6504), (39.7392, -104.9903)),
    "seattle_portland": ((47.6062, -122.3321), (45.5152, -122.6784)),
    "miami_orlando": ((25.7617, -80.1918), (28.5383, -81.3792)),
    "los_angeles_las_vegas": ((34.0522, -118.2437), (36.1699, -115.1398)),
}


def _relative_difference(local: float, hosted: float) -> float:
    return round((local - hosted) / hosted, 6)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8004")
    parser.add_argument(
        "--graph-version", default="geofabrik-us-20261009-valhalla-3.9.1-auto"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("var/benchmarks/national-profile.json")
    )
    args = parser.parse_args()
    local = LocalValhalla(args.url, args.graph_version, request_timeout_seconds=14)
    hosted = OpenRouteServiceAdapter(routing_key())
    results = []
    try:
        for name, (raw_start, raw_finish) in CASES.items():
            points = (Coordinates(*raw_start), Coordinates(*raw_finish))
            started = perf_counter()
            local_route = local.route(points, RequestBudget())
            local_seconds = perf_counter() - started
            started = perf_counter()
            ors_route = hosted.route(points, RequestBudget())
            ors_seconds = perf_counter() - started
            local_meters = float(local_route.distance_meters)
            ors_meters = float(ors_route.distance_meters)
            local_duration = float(local_route.duration_seconds)
            ors_duration = float(ors_route.duration_seconds)
            results.append(
                {
                    "case": name,
                    "local_distance_meters": local_meters,
                    "ors_distance_meters": ors_meters,
                    "distance_relative_difference": _relative_difference(
                        local_meters, ors_meters
                    ),
                    "local_duration_seconds": local_duration,
                    "ors_duration_seconds": ors_duration,
                    "duration_relative_difference": _relative_difference(
                        local_duration, ors_duration
                    ),
                    "local_max_snap_meters": max(
                        float(item.snap_distance_meters)
                        for item in local_route.waypoints
                    ),
                    "ors_max_snap_meters": max(
                        float(item.snap_distance_meters) for item in ors_route.waypoints
                    ),
                    "local_request_seconds": round(local_seconds, 3),
                    "ors_request_seconds": round(ors_seconds, 3),
                }
            )
            print(json.dumps(results[-1]), flush=True)
    finally:
        local.close()
        hosted.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"graph_version": args.graph_version, "cases": results}, indent=2)
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
