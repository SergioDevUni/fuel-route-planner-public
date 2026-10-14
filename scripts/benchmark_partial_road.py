"""Measure completed-source local trips under one 15-second planning budget."""

import argparse
import json
import os
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from app.api.inputs import resolve_inputs  # noqa: E402
from app.api.validation import TripInput  # noqa: E402
from app.domain.routing import Coordinates  # noqa: E402
from app.planning.budget import RequestBudget  # noqa: E402
from app.planning.candidates import CandidateIndex, StationCandidate  # noqa: E402
from app.planning.endpoint_block_cache import EndpointBlockCache  # noqa: E402
from app.planning.graph_search import search_fuel_graph  # noqa: E402
from app.planning.national_graph import (  # noqa: E402
    build_trip_road_graph,
    completed_road_points,
)
from app.planning.national_scope import screen_csv_offers  # noqa: E402
from app.planning.trip_results import calculate_fuel  # noqa: E402
from app.routing.local_valhalla import LocalValhalla  # noqa: E402
from app.stations.models import DatasetVersion  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VERSION = "geofabrik-us-20261009-valhalla-3.9.1-auto"
DATABASE = ROOT / "var/road_nationwide/station_edges_v2_live.db"
if not DATABASE.is_file():
    DATABASE = ROOT / "var/road_nationwide/station_edges.db"


def _trip(start: str, finish: str, state: str, initial: str) -> TripInput:
    return TripInput(
        {"type": "city", "city": start, "state": state},
        {"type": "city", "city": finish, "state": state},
        Decimal(initial),
        Decimal("3.20"),
        Decimal(0),
        None,
        None,
        "balanced",
        Decimal(30),
    )


def measure(
    case: str,
    *,
    warm: bool = False,
    profile_search: bool = False,
    use_endpoint_cache: bool = True,
    url: str = "http://127.0.0.1:8004",
    request_timeout_seconds: float = 10,
) -> dict[str, object]:
    active = DatasetVersion.objects.get(status="active")
    index = CandidateIndex(ROOT / "var/station_candidates", active.source_sha256)
    completed = completed_road_points(
        DATABASE, index.version, VERSION, active_point_hash=index.road_point_hash
    )
    if case == "central_florida":
        trip = _trip("Orlando", "Tampa", "FL", "5")
        stations = tuple(
            item
            for item in index.stations
            if item.state == "FL"
            and 27 <= item.coordinates.latitude <= 29.5
            and item.coordinates in completed
        )
    elif case == "all_florida":
        trip = _trip("Orlando", "Tampa", "FL", "5")
        stations = tuple(
            item
            for item in index.stations
            if item.state == "FL" and item.coordinates in completed
        )
    elif case == "orlando_jacksonville":
        trip = _trip("Orlando", "Jacksonville", "FL", "5")
        stations = tuple(
            item
            for item in index.stations
            if item.state == "FL"
            and 28 <= item.coordinates.latitude <= 31
            and -83 <= item.coordinates.longitude <= -80
            and item.coordinates in completed
        )
    elif case == "houston_dallas":
        trip = _trip("Houston", "Dallas", "TX", "5")
        stations = tuple(
            item
            for item in index.stations
            if item.state == "TX"
            and 28 <= item.coordinates.latitude <= 34
            and -99 <= item.coordinates.longitude <= -94
            and item.coordinates in completed
        )
    elif case in ("albuquerque_denver", "albuquerque_denver_seed"):
        trip = TripInput(
            {"type": "city", "city": "Albuquerque", "state": "NM"},
            {"type": "city", "city": "Denver", "state": "CO"},
            Decimal(5),
            Decimal("3.20"),
            Decimal(0),
            None,
            None,
            "balanced",
            Decimal(30),
        )
        priority_path = (
            ROOT
            / "var/road_nationwide"
            / (
                "priority_albuquerque_denver_seed.json"
                if case.endswith("_seed")
                else "priority_albuquerque_denver.json"
            )
        )
        priority = json.loads(priority_path.read_text())
        if case.endswith("_seed"):
            with sqlite3.connect(DATABASE) as connection:
                point_coordinates = {
                    point_id: Coordinates(latitude, longitude)
                    for point_id, latitude, longitude in connection.execute(
                        "SELECT point_id,latitude,longitude FROM point"
                    )
                }
            wanted = {point_coordinates[point_id] for point_id in priority["point_ids"]}
            stations = tuple(
                item for item in index.stations if item.coordinates in wanted
            )
        else:
            start, finish = resolve_inputs(trip)
            scope = screen_csv_offers(
                index.stations,
                start.coordinates,
                finish.coordinates,
                Decimal(5),
                Decimal(0),
                Decimal(priority["incumbent_purchase_cost"]),
            )
            stations = tuple(item.station for item in scope.possible_offers)
        pending = sum(item.coordinates not in completed for item in stations)
        if pending:
            return {
                "case": case,
                "status": "priority_pending",
                "pending_offers": pending,
            }
    else:
        raise ValueError("unknown partial-road case")
    start, finish = resolve_inputs(trip)
    budget = RequestBudget(
        deadline_seconds=600 if warm else 120 if profile_search else 15
    )
    local = LocalValhalla(
        url, VERSION, request_timeout_seconds=60 if warm else request_timeout_seconds
    )
    endpoint_cache = (
        EndpointBlockCache(DATABASE.parent / "endpoint_blocks", local)
        if use_endpoint_cache
        else None
    )
    started = time.monotonic()
    result: dict[str, object] = {
        "case": case,
        "offers": len(stations),
        "road_points": len({item.coordinates for item in stations}),
        "completed_sources": len(completed),
        "warm_run": warm,
        "profile_search": profile_search,
        "endpoint_cache": use_endpoint_cache,
        "local_url": url,
        "request_timeout_seconds": request_timeout_seconds,
    }
    try:
        if warm:
            build_trip_road_graph(
                DATABASE,
                stations,
                start.coordinates,
                finish.coordinates,
                local,
                budget,
                candidate_version=index.version,
                active_point_hash=index.road_point_hash,
                initial_gallons=trip.initial_fuel_gallons,
                reserve_gallons=trip.reserve_gallons,
                endpoint_cache=endpoint_cache,
                sparse=len(stations) > 200,
            )
            result["status"] = "warmed"
            result["warm_seconds"] = round(time.monotonic() - started, 3)
            return result
        baseline = local.route((start.coordinates, finish.coordinates), budget)
        result["baseline_seconds"] = round(time.monotonic() - started, 3)
        selection = build_trip_road_graph(
            DATABASE,
            stations,
            start.coordinates,
            finish.coordinates,
            local,
            budget,
            candidate_version=index.version,
            active_point_hash=index.road_point_hash,
            initial_gallons=trip.initial_fuel_gallons,
            reserve_gallons=trip.reserve_gallons,
            endpoint_cache=endpoint_cache,
            sparse=len(stations) > 200,
        )
        result["graph_seconds"] = round(time.monotonic() - started, 3)
        search = search_fuel_graph(
            selection.graph,
            trip.initial_fuel_gallons,
            trip.reserve_gallons,
            max_total_seconds=baseline.duration_seconds + Decimal(1800),
            max_expansions=100_000,
            stop_when=lambda: budget.remaining_seconds <= (15 if profile_search else 2),
        )
        result["search_seconds"] = round(time.monotonic() - started, 3)
        result["search_complete"] = search.complete
        result["expansions"] = search.expansions
        if search.solution is not None:
            by_id: dict[str, StationCandidate] = {
                item.station_id: item for item in selection.retained
            }
            chosen = tuple(by_id[item] for item in search.solution.station_ids)
            route = local.route(
                (
                    start.coordinates,
                    *(item.coordinates for item in chosen),
                    finish.coordinates,
                ),
                budget,
            )
            fuel = calculate_fuel(
                chosen,
                tuple(leg.distance_meters for leg in route.legs),
                trip.initial_fuel_gallons,
                trip.reserve_gallons,
            )
            result.update(
                {
                    "status": "verified",
                    "final_seconds": round(time.monotonic() - started, 3),
                    "station_ids": [item.station_id for item in chosen],
                    "extra_minutes": round(
                        float(
                            (route.duration_seconds - baseline.duration_seconds) / 60
                        ),
                        3,
                    ),
                    "purchase_cost": str(fuel.exact_purchase_cost),
                }
            )
        else:
            result["status"] = "no_solution"
    except Exception as error:
        result["status"] = "error"
        result["error"] = type(error).__name__
        result["reason"] = str(error)
    finally:
        local.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "case",
        choices=(
            "central_florida",
            "all_florida",
            "orlando_jacksonville",
            "houston_dallas",
            "albuquerque_denver_seed",
            "albuquerque_denver",
        ),
    )
    parser.add_argument(
        "--warm",
        action="store_true",
        help="Fill endpoint cache outside the planning deadline",
    )
    parser.add_argument(
        "--profile-search",
        action="store_true",
        help="Measure the full search outside the planning deadline",
    )
    parser.add_argument("--no-endpoint-cache", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8004")
    parser.add_argument("--request-timeout-seconds", type=float, default=10)
    args = parser.parse_args()
    print(
        json.dumps(
            measure(
                args.case,
                warm=args.warm,
                profile_search=args.profile_search,
                use_endpoint_cache=not args.no_endpoint_cache,
                url=args.url,
                request_timeout_seconds=args.request_timeout_seconds,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
