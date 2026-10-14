"""Research a two-hosted-call fresh-trip path without baseline directions.

The first call is a directed ORS matrix; the second verifies its selected
waypoints. This is deliberately outside the API until candidate recall and
matrix-versus-directions time policy are measured. No pending_check route is
returned as success.
"""

import argparse
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from app.api.inputs import resolve_inputs  # noqa: E402
from app.api.validation import TripInput  # noqa: E402
from app.domain.routing import Route, RouteLeg, RouteWaypoint  # noqa: E402
from app.planning.budget import RequestBudget  # noqa: E402
from app.planning.candidates import CandidateIndex  # noqa: E402
from app.planning.fuel import FuelShortfall, optimize_fuel  # noqa: E402
from app.planning.graph_search import search_fuel_graph  # noqa: E402
from app.planning.matrix_candidates import (  # noqa: E402
    matrix_points,
    select_matrix_candidates,
)
from app.planning.road_graph import graph_from_matrix  # noqa: E402
from app.routing.client import OpenRouteServiceAdapter  # noqa: E402
from app.routing.geometry import geodesic_meters  # noqa: E402
from app.stations.models import DatasetVersion  # noqa: E402
from scripts.benchmark_matrix_selector import CASES  # noqa: E402
from scripts.check_pilot_routes import routing_key  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def study(case_name: str) -> dict:
    start_spec, finish_spec, initial = CASES[case_name]
    request = TripInput(
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
    start, finish = resolve_inputs(request)
    candidates = CandidateIndex(
        ROOT / "var/station_candidates",
        DatasetVersion.objects.get(status="active").source_sha256,
    )
    chord = geodesic_meters(start.coordinates, finish.coordinates)
    # This straight geometry only ranks station candidates. It never becomes
    # a returned route or a travel-time baseline.
    projection = Route(
        (start.coordinates, finish.coordinates),
        (
            RouteWaypoint(start.coordinates, start.coordinates, 0, Decimal(0)),
            RouteWaypoint(finish.coordinates, finish.coordinates, 1, Decimal(0)),
        ),
        (RouteLeg(chord, Decimal(0)),),
        chord,
        Decimal(0),
        "research-chord-only",
        datetime.now(UTC),
    )
    along = candidates.along_route(projection, 80_000)
    envelope = candidates.within_detour_envelope(projection, chord * 2)
    along_ids = {item.station.station_id for item in along}
    selected = select_matrix_candidates(
        (),
        along
        + tuple(item for item in envelope if item.station.station_id not in along_ids),
        chord,
        offset_price_penalty_per_mile=Decimal("0.02"),
    )
    if not selected:
        return {"case": case_name, "status": "no_candidates"}
    points, offer_indices = matrix_points(
        start.coordinates, selected, finish.coordinates
    )
    budget = RequestBudget(deadline_seconds=45)
    adapter = OpenRouteServiceAdapter(routing_key())
    started = perf_counter()
    try:
        matrix = adapter.matrix(points, budget)
        graph = graph_from_matrix(
            tuple(item.station for item in selected),
            matrix,
            f"matrix-first-research:{candidates.version}",
            station_point_indices=offer_indices,
        )
        assert graph.durations_seconds is not None
        direct_seconds = graph.durations_seconds[0][-1]
        if direct_seconds is None:
            raise ValueError("matrix direct leg is unreachable")
        result = search_fuel_graph(
            graph,
            request.initial_fuel_gallons,
            request.reserve_gallons,
            max_total_seconds=direct_seconds + Decimal(1800),
            max_expansions=20_000,
        )
        if result.solution is None:
            return {
                "case": case_name,
                "status": "no_matrix_plan",
                "hosted_attempts": budget.attempts,
            }
        by_id = {item.station.station_id: item for item in selected}
        chosen = tuple(by_id[item] for item in result.solution.station_ids)
        verified = adapter.route(
            (
                start.coordinates,
                *(item.station.coordinates for item in chosen),
                finish.coordinates,
            ),
            budget,
        )
        snaps_ok = all(
            point.snap_distance_meters <= 250 for point in verified.waypoints[1:-1]
        )
        time_ok = verified.duration_seconds <= direct_seconds + Decimal(1800)
        try:
            fuel_by_id = {station.station_id: station for station in graph.stations}
            final_fuel = optimize_fuel(
                tuple(fuel_by_id[item] for item in result.solution.station_ids),
                tuple(leg.distance_meters for leg in verified.legs),
                request.initial_fuel_gallons,
                request.reserve_gallons,
            )
        except FuelShortfall:
            final_fuel = None
        return {
            "case": case_name,
            "status": (
                "verified"
                if snaps_ok and time_ok and final_fuel is not None
                else "bad_waypoint_snap"
                if not snaps_ok
                else "duration_limit"
                if not time_ok
                else "fuel_shortfall"
            ),
            "hosted_attempts": budget.attempts,
            "reserve_attempts": budget.max_attempts - budget.attempts,
            "matrix_points": len(points),
            "along_candidates": len(along),
            "envelope_candidates": len(envelope),
            "complete": result.complete,
            "expansions": result.expansions,
            "selected_ids": result.solution.station_ids,
            "matrix_cost": str(result.solution.purchase_cost),
            "verified_cost": str(final_fuel.exact_purchase_cost)
            if final_fuel
            else None,
            "matrix_direct_minutes": round(float(direct_seconds / 60), 2),
            "verified_minutes": round(float(verified.duration_seconds / 60), 2),
            "verified_extra_vs_matrix_minutes": round(
                float((verified.duration_seconds - direct_seconds) / 60), 2
            ),
            "elapsed_seconds": round(perf_counter() - started, 3),
        }
    finally:
        adapter.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="+", choices=CASES)
    args = parser.parse_args()
    for case in args.cases:
        try:
            print(json.dumps(study(case)))
        except Exception as error:
            print(json.dumps({"case": case, "status": type(error).__name__}))


if __name__ == "__main__":
    main()
