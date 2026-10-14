"""Check road snaps for site-verified regional pilot points with one ORS call."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.routing.client import OpenRouteServiceAdapter

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ("chicago", "imperial_ca")


def routing_key() -> str:
    key = os.environ.get("ORS_API_KEY", "").strip()
    if key:
        return key
    local_env = ROOT / ".env"
    if local_env.exists():
        for line in local_env.read_text(encoding="utf-8").splitlines():
            if line.startswith("ORS_API_KEY="):
                return line.partition("=")[2].strip().strip('"').strip("'")
    raise ValueError("ORS_API_KEY is required for live snap checks")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", choices=REGIONS, default="chicago")
    args = parser.parse_args()
    sites = ROOT / f"var/pilot/{args.region}_site_verified.json"
    output = ROOT / f"var/pilot/{args.region}_snap_check.json"
    source_bytes = sites.read_bytes()
    stations = json.loads(source_bytes)["stations"]
    waypoints = tuple(
        Coordinates(item["latitude"], item["longitude"]) for item in stations
    )
    adapter = OpenRouteServiceAdapter(routing_key())
    try:
        route = adapter.route(waypoints, RequestBudget(deadline_seconds=30))
    finally:
        adapter.close()
    observations = [
        {
            "opis_id": item["opis_id"],
            "requested_latitude": waypoint.requested.latitude,
            "requested_longitude": waypoint.requested.longitude,
            "routed_latitude": waypoint.routed.latitude,
            "routed_longitude": waypoint.routed.longitude,
            "snap_distance_meters": str(waypoint.snap_distance_meters),
            "road_access_status": "pending_check",
        }
        for item, waypoint in zip(stations, route.waypoints, strict=True)
    ]
    document = {
        "site_file_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "provider": "openrouteservice",
        "engine_version": route.engine_version,
        "fetched_at": route.fetched_at.isoformat(),
        "route_distance_meters": str(route.distance_meters),
        "purpose": "road-near-site snap check only; route order is artificial",
        "observations": observations,
    }
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"Checked {len(observations)} site road snaps; access remains pending_check")
    for item in observations:
        print(item["opis_id"], item["snap_distance_meters"], "meters")


if __name__ == "__main__":
    main()
