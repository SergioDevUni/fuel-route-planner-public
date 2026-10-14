"""Check the full-cache gate, then optionally replay local-only API trips."""

import argparse
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from app.domain.routing import Coordinates
from app.planning.road_cache_identity import SCHEMA_VERSION as UPDATE_SAFE_SCHEMA
from app.planning.road_cache_identity import point_set_hash

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "var/road_nationwide/station_edges_v2_live.db"
if not DATABASE.is_file():
    DATABASE = ROOT / "var/road_nationwide/station_edges.db"
MARKER = DATABASE.with_suffix(".complete.json")
MANIFEST = ROOT / "var/station_candidates/manifest.json"
ROAD_VERSION = "geofabrik-us-20261009-valhalla-3.9.1-auto"


def inspect() -> dict[str, object]:
    candidate = json.loads(MANIFEST.read_text(encoding="utf-8"))
    with closing(sqlite3.connect(DATABASE)) as connection:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("road cache failed SQLite integrity check")
        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        points = connection.execute("SELECT COUNT(*) FROM point").fetchone()[0]
        update_safe = metadata.get("schema") == UPDATE_SAFE_SCHEMA
        active_clause = " AND active = 1" if update_safe else ""
        located = connection.execute(
            "SELECT COUNT(*) FROM point WHERE snap_meters IS NOT NULL" + active_clause
        ).fetchone()[0]
        complete = connection.execute(
            "SELECT COUNT(*) FROM complete_source"
            + (
                " JOIN point USING(point_id) WHERE point.active = 1"
                if update_safe
                else ""
            )
        ).fetchone()[0]
        edges = connection.execute("SELECT COUNT(*) FROM edge").fetchone()[0]
        point_hash = (
            point_set_hash(
                Coordinates(lat, lon)
                for lat, lon in connection.execute(
                    "SELECT latitude, longitude FROM point WHERE active = 1"
                )
            )
            if update_safe
            else None
        )
    if metadata.get("road_graph_version") != ROAD_VERSION or (
        metadata.get("active_point_hash") != point_hash
        if update_safe
        else metadata.get("candidate_hash") != candidate["candidate_sha256"]
        or metadata.get("source_hash") != candidate["source_sha256"]
    ):
        raise RuntimeError("road cache and active candidate/graph versions differ")
    result: dict[str, object] = {
        "points": points,
        "road_located_points": located,
        "completed_sources": complete,
        "directed_edges": edges,
        "completion_marker": MARKER.is_file(),
        "status": "pending",
    }
    if not MARKER.is_file():
        return result
    marker = json.loads(MARKER.read_text(encoding="utf-8"))
    if (
        (
            marker.get("active_point_hash") != point_hash
            if update_safe
            else (
                marker.get("candidate_version") != candidate["candidate_sha256"]
                or marker.get("source_hash") != candidate["source_sha256"]
            )
        )
        or marker.get("graph_version") != ROAD_VERSION
        or marker.get("completed_sources") != located
        or complete != located
        or marker.get("stored_directed_legs") != edges
    ):
        raise RuntimeError("completion marker does not match the road cache")
    result["status"] = "cache_complete"
    return result


def verify_trips(url: str = "http://127.0.0.1:8004") -> list[dict[str, object]]:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ["FUEL_NATIONAL_VALHALLA_URL"] = url
    os.environ["FUEL_NATIONAL_VALHALLA_VERSION"] = ROAD_VERSION
    os.environ["FUEL_NATIONAL_LOCAL_ROUTER"] = "1"
    os.environ["ORS_API_KEY"] = ""
    import django

    django.setup()
    from django.test import Client

    cases = (
        ("orlando_tampa", "Orlando", "FL", "Tampa", "FL", "5"),
        ("albuquerque_denver", "Albuquerque", "NM", "Denver", "CO", "5"),
        ("chicago_st_louis", "Chicago", "IL", "St. Louis", "MO", "5"),
        ("portland_seattle", "Portland", "OR", "Seattle", "WA", "10"),
    )
    results: list[dict[str, object]] = []
    for name, origin, origin_state, destination, destination_state, initial in cases:
        body = {
            "start": {"type": "city", "city": origin, "state": origin_state},
            "finish": {
                "type": "city",
                "city": destination,
                "state": destination_state,
            },
            "initial_fuel_gallons": initial,
            "initial_fuel_price_per_gallon": "3.20",
        }
        started = time.monotonic()
        response = Client().post(
            "/api/v1/trips", data=json.dumps(body), content_type="application/json"
        )
        data = response.json()
        elapsed = time.monotonic() - started
        result = {
            "case": name,
            "http": response.status_code,
            "elapsed_seconds": round(elapsed, 3),
            "status": data.get("status"),
            "selection": data.get("assumptions", {}).get("station_selection"),
            "paid_station_ids": [
                stop["station_id"] for stop in data.get("fuel_stops", [])
            ],
            "purchase_cost": data.get("costs", {}).get("fuel_purchases_cost"),
            "extra_duration_seconds": data.get("route", {}).get(
                "extra_duration_seconds"
            ),
            "error": data.get("error", {}).get("code"),
        }
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-trips", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8004")
    args = parser.parse_args()
    state = inspect()
    if args.verify_trips and state["status"] == "cache_complete":
        state["trips"] = verify_trips(args.url)
        if any(
            item["http"] != 200
            or item["status"] != "verified"
            or item["elapsed_seconds"] > 15.5
            or item["extra_duration_seconds"] is None
            or float(item["extra_duration_seconds"]) > 1800
            for item in state["trips"]
        ):
            state["status"] = "trip_validation_failed"
    print(json.dumps(state, sort_keys=True))
    if args.verify_trips and state["status"] != "cache_complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
