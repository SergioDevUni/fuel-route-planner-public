"""Compare production 40-point selection with a larger live directed matrix.

One baseline directions call and one matrix call measure candidate recall
inside a larger candidate set.
"""

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path
from time import perf_counter

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from app.api.inputs import resolve_inputs  # noqa: E402
from app.api.validation import TripInput  # noqa: E402
from app.domain.road import RoadDistanceMatrix  # noqa: E402
from app.domain.units import METERS_PER_MILE  # noqa: E402
from app.planning.budget import RequestBudget  # noqa: E402
from app.planning.candidates import CandidateIndex  # noqa: E402
from app.planning.graph_search import search_fuel_graph  # noqa: E402
from app.planning.matrix_candidates import (  # noqa: E402
    matrix_points,
    select_matrix_candidates,
)
from app.planning.road_graph import graph_from_matrix  # noqa: E402
from app.routing.client import OpenRouteServiceAdapter  # noqa: E402
from app.stations.models import DatasetVersion  # noqa: E402
from scripts.benchmark_matrix_selector import CASES  # noqa: E402
from scripts.check_pilot_routes import routing_key  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def subset_matrix(
    matrix: RoadDistanceMatrix,
    all_points: tuple,
    wanted_points: tuple,
) -> RoadDistanceMatrix:
    lookup = {point: index for index, point in enumerate(all_points)}
    indices = tuple(lookup[point] for point in wanted_points)
    return RoadDistanceMatrix(
        tuple(
            tuple(matrix.distances_meters[source][target] for target in indices)
            for source in indices
        ),
        tuple(matrix.source_snap_meters[index] for index in indices),
        tuple(matrix.destination_snap_meters[index] for index in indices),
        (
            tuple(
                tuple(matrix.durations_seconds[source][target] for target in indices)
                for source in indices
            )
            if matrix.durations_seconds is not None
            else None
        ),
    )


def study(
    case_name: str, max_locations: int, max_expansions: int, *, time_only: bool = False
) -> dict:
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
    active = DatasetVersion.objects.get(status="active")
    candidates = CandidateIndex(ROOT / "var/station_candidates", active.source_sha256)
    start, finish = resolve_inputs(request)
    adapter = OpenRouteServiceAdapter(
        routing_key(), matrix_location_limit=max_locations
    )
    budget = RequestBudget(deadline_seconds=45)
    try:
        baseline = adapter.route((start.coordinates, finish.coordinates), budget)
        from app.planning.corridor_selection import choose_corridor
        from app.planning.trip_context import PlanningContext

        provisional = choose_corridor(
            PlanningContext(adapter, candidates),
            baseline,
            request.initial_fuel_gallons,
            request.reserve_gallons,
        )
        max_extra = (
            None
            if time_only
            else min(
                request.max_detour_miles * METERS_PER_MILE,
                baseline.distance_meters * request.max_detour_ratio,
            )
        )
        radius = min(
            80_000.0,
            max(32_000.0, float(max_extra) * 2 if max_extra is not None else 80_000.0),
        )
        along = candidates.along_route(baseline, radius)
        along_ids = {item.station.station_id for item in along}
        off_route = tuple(
            item
            for item in candidates.within_detour_envelope(
                baseline,
                (
                    baseline.distance_meters + max_extra
                    if max_extra is not None
                    else max(
                        baseline.distance_meters,
                        (
                            baseline.duration_seconds
                            + request.max_extra_duration_minutes * 60
                        )
                        * 100,
                    )
                ),
            )
            if item.station.station_id not in along_ids
        )
        alternatives = along + off_route
        expanded = select_matrix_candidates(
            provisional,
            alternatives,
            baseline.distance_meters,
            max_locations=max_locations,
            max_offers=max_locations + 12,
            offset_price_penalty_per_mile=Decimal("0.02") if time_only else Decimal(0),
        )
        if not expanded:
            raise ValueError("no expanded matrix candidates")
        expanded_points, _ = matrix_points(
            start.coordinates, expanded, finish.coordinates
        )
        started = perf_counter()
        large_matrix = adapter.matrix(expanded_points, budget)
        matrix_seconds = perf_counter() - started
        results = []
        for limit in (40, max_locations):
            selected = select_matrix_candidates(
                provisional,
                alternatives,
                baseline.distance_meters,
                max_locations=limit,
                max_offers=limit + 12,
                offset_price_penalty_per_mile=(
                    Decimal("0.02") if time_only else Decimal(0)
                ),
            )
            assert selected is not None
            points, indices = matrix_points(
                start.coordinates, selected, finish.coordinates
            )
            graph = graph_from_matrix(
                tuple(item.station for item in selected),
                subset_matrix(large_matrix, expanded_points, points),
                f"scope-study:{case_name}:{limit}",
                station_point_indices=indices,
            )
            started = perf_counter()
            result = search_fuel_graph(
                graph,
                request.initial_fuel_gallons,
                request.reserve_gallons,
                max_total_meters=(
                    baseline.distance_meters + max_extra
                    if max_extra is not None
                    else None
                ),
                max_total_seconds=(
                    baseline.duration_seconds + request.max_extra_duration_minutes * 60
                ),
                max_expansions=max_expansions,
            )
            search_seconds = perf_counter() - started
            solution = result.solution
            results.append(
                {
                    "limit": limit,
                    "offers": len(selected),
                    "points": len(points),
                    "retained_offers": len(graph.stations),
                    "complete": result.complete,
                    "expansions": result.expansions,
                    "search_seconds": round(search_seconds, 3),
                    "estimated_purchase_cost": str(solution.purchase_cost)
                    if solution
                    else None,
                    "selected_ids": solution.station_ids if solution else (),
                }
            )
        return {
            "case": case_name,
            "policy": "time_only" if time_only else "time_and_distance",
            "baseline_miles": round(
                float(baseline.distance_meters / METERS_PER_MILE), 3
            ),
            "along_candidates": len(along),
            "off_route_candidates": len(off_route),
            "largest_matrix_points": len(expanded_points),
            "largest_matrix_cells": len(expanded_points) ** 2,
            "matrix_seconds": round(matrix_seconds, 3),
            "hosted_attempts": budget.attempts,
            "comparisons": results,
        }
    finally:
        adapter.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=CASES)
    parser.add_argument("--max-locations", type=int, default=50)
    parser.add_argument("--max-expansions", type=int, default=20_000)
    parser.add_argument("--time-only", action="store_true")
    args = parser.parse_args()
    if not 41 <= args.max_locations <= 59:
        parser.error("max-locations must be between 41 and 59")
    if not 1 <= args.max_expansions <= 100_000:
        parser.error("max-expansions must be between 1 and 100000")
    print(
        json.dumps(
            study(
                args.case,
                args.max_locations,
                args.max_expansions,
                time_only=args.time_only,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
