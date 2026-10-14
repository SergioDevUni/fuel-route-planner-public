import argparse
import os
from decimal import Decimal
from time import perf_counter

from app.api.inputs import resolve_inputs
from app.domain.ledger import usd_cents
from app.planning.budget import RequestBudget
from app.planning.candidates import CandidateIndex
from app.planning.endpoint_block_cache import EndpointBlockCache
from app.planning.fuel_graph import DirectedFuelGraph
from app.planning.graph_search import search_fuel_graph
from app.planning.mip_search import search_fuel_graph_mip
from app.planning.national_graph import build_trip_road_graph, completed_road_points
from app.planning.national_scope import screen_csv_offers
from app.planning.trip_results import calculate_fuel
from app.routing.client import OpenRouteServiceAdapter
from app.routing.local_valhalla import LocalValhalla
from scripts.benchmarking.cases import BENCHMARK_CASES
from scripts.benchmarking.measurement import trip_input
from scripts.benchmarking.policy import DATABASE, GRAPH_VERSION


def local_reference(
    name: str, rows: list[dict], index: CandidateIndex, args: argparse.Namespace
) -> dict:
    case = BENCHMARK_CASES[name]
    successes = [
        row
        for row in rows
        if row["case"] == name
        and row["contract_ok"]
        and row.get("profile") == "ors_fastest_driving_car"
    ]
    if not successes:
        return {"case": name, "status": "no_comparable_incumbent"}
    incumbent = min(successes, key=lambda row: Decimal(row["purchase_cost"]))
    trip = trip_input(case)
    start, finish = resolve_inputs(trip)
    completed = completed_road_points(
        DATABASE, index.version, GRAPH_VERSION, active_point_hash=index.road_point_hash
    )
    scope = screen_csv_offers(
        index.stations,
        start.coordinates,
        finish.coordinates,
        trip.initial_fuel_gallons,
        trip.reserve_gallons,
        Decimal(incumbent["purchase_cost"]),
        price_access_bound=True,
    )
    possible = tuple(item.station for item in scope.possible_offers)
    available = tuple(
        station for station in possible if station.coordinates in completed
    )
    missing = len(
        {
            station.coordinates
            for station in possible
            if station.coordinates not in completed
        }
    )
    result = {
        "case": name,
        "us_csv_offers": len(index.stations),
        "safely_excluded_offers": scope.excluded_by_cost,
        "possible_offers": len(possible),
        "available_offers": len(available),
        "missing_possible_source_points": missing,
        "completed_source_points": len(completed),
        "reference_cost": incumbent["purchase_cost"],
        "reference_kind": "best_observed_verified",
        "independent_reference_available": False,
        "zero_purchase_case": Decimal(incumbent["purchase_cost"]) == 0,
    }
    if result["zero_purchase_case"]:
        result["status"] = "zero_purchase_minimum"
        return result
    if not available:
        result["status"] = "no_available_road_sources"
        return result
    local = LocalValhalla(args.url, GRAPH_VERSION, request_timeout_seconds=8)
    started = perf_counter()
    budget = RequestBudget(deadline_seconds=args.reference_seconds)
    solutions = []
    try:
        baseline = local.route((start.coordinates, finish.coordinates), budget)
        selection = build_trip_road_graph(
            DATABASE,
            available,
            start.coordinates,
            finish.coordinates,
            local,
            budget,
            candidate_version=index.version,
            active_point_hash=index.road_point_hash,
            initial_gallons=trip.initial_fuel_gallons,
            reserve_gallons=trip.reserve_gallons,
            endpoint_cache=EndpointBlockCache(
                DATABASE.parent / "endpoint_blocks", local
            ),
            sparse=len(available) > 200,
        )
        result["unavailable_csv_ids"] = list(selection.unavailable_ids)
        limit = baseline.duration_seconds + 1800
        search = search_fuel_graph(
            selection.graph,
            trip.initial_fuel_gallons,
            trip.reserve_gallons,
            max_total_seconds=limit,
            max_expansions=500_000,
            stop_when=lambda: budget.remaining_seconds <= 4,
        )
        result.update(
            {"search_complete": search.complete, "search_expansions": search.expansions}
        )
        if search.solution:
            solutions.append(("extended_production_search", search.solution))
        result["extended_search_seconds"] = round(perf_counter() - started, 3)
        if isinstance(selection.graph, DirectedFuelGraph):
            mip = search_fuel_graph_mip(
                selection.graph,
                trip.initial_fuel_gallons,
                trip.reserve_gallons,
                max_total_seconds=limit,
                max_solver_seconds=args.mip_seconds,
            )
            result["independent_solver_complete"] = mip.complete
            if mip.solution:
                solutions.append(("independent_mip", mip.solution))
        else:
            result["independent_solver_complete"] = False
        by_id = {station.station_id: station for station in available}
        adapter = OpenRouteServiceAdapter(os.environ["ORS_API_KEY"])
        verify_budget = RequestBudget(deadline_seconds=30)
        try:
            direct = adapter.route(
                (start.coordinates, finish.coordinates), verify_budget
            )
            candidates = []
            seen = set()
            for source, solution in solutions:
                if solution.station_ids in seen:
                    if source == "independent_mip":
                        verified = next(
                            (
                                candidate
                                for candidate in candidates
                                if candidate.get("valid")
                                and candidate.get("station_ids")
                                == list(solution.station_ids)
                            ),
                            None,
                        )
                        if verified:
                            result["independent_reference_available"] = True
                            candidates.append(
                                {
                                    **verified,
                                    "source": source,
                                    "reuses_hosted_verification": True,
                                }
                            )
                    continue
                if len(solution.station_ids) > 48:
                    continue
                seen.add(solution.station_ids)
                stations = tuple(
                    by_id[station_id] for station_id in solution.station_ids
                )
                try:
                    route = adapter.route(
                        (
                            start.coordinates,
                            *(station.coordinates for station in stations),
                            finish.coordinates,
                        ),
                        verify_budget,
                    )
                    if route.duration_seconds > direct.duration_seconds + 1800:
                        candidates.append(
                            {
                                "source": source,
                                "valid": False,
                                "reason": "hosted_duration_limit",
                            }
                        )
                        continue
                    if any(
                        point.snap_distance_meters
                        > (
                            1000
                            if station.location_precision == "city_representative"
                            else 250
                        )
                        for point, station in zip(
                            route.waypoints[1:-1], stations, strict=True
                        )
                    ) or any(
                        point.snap_distance_meters > 1000
                        for point in (route.waypoints[0], route.waypoints[-1])
                    ):
                        candidates.append(
                            {"source": source, "valid": False, "reason": "snap_limit"}
                        )
                        continue
                    fuel = calculate_fuel(
                        stations,
                        tuple(leg.distance_meters for leg in route.legs),
                        trip.initial_fuel_gallons,
                        trip.reserve_gallons,
                    )
                    cost = usd_cents(fuel.exact_purchase_cost)
                    candidates.append(
                        {
                            "source": source,
                            "valid": True,
                            "cost": str(cost),
                            "station_ids": list(solution.station_ids),
                        }
                    )
                    if source == "independent_mip":
                        result["independent_reference_available"] = True
                    if cost < Decimal(result["reference_cost"]):
                        result["reference_cost"] = str(cost)
                        result["reference_kind"] = source + "_hosted_verified"
                except Exception as error:
                    candidates.append(
                        {
                            "source": source,
                            "valid": False,
                            "reason": type(error).__name__,
                        }
                    )
            result["verified_offline_candidates"] = candidates
            result["offline_hosted_attempts"] = verify_budget.attempts
        finally:
            result["offline_hosted_attempts"] = verify_budget.attempts
            adapter.close()
        result["status"] = "best_known_comparison_only"
    except Exception as error:
        result["status"] = "offline_reference_unavailable"
        result["error"] = type(error).__name__
    finally:
        local.close()
        result["offline_seconds"] = round(perf_counter() - started, 3)
    return result
