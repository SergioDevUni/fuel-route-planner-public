import argparse
import os
from decimal import Decimal
from time import perf_counter

from app.api.inputs import resolve_inputs
from app.domain.ledger import usd_cents
from app.planning.budget import RequestBudget
from app.planning.candidates import CandidateIndex
from app.planning.fuel_graph import DirectedFuelGraph
from app.planning.graph_search import search_fuel_graph
from app.planning.mip_search import search_fuel_graph_mip
from app.planning.national_scope import screen_csv_offers
from app.planning.trip_results import calculate_fuel
from app.routing.client import OpenRouteServiceAdapter
from scripts.benchmarking.capture import HOSTED_GRAPHS
from scripts.benchmarking.cases import BENCHMARK_CASES
from scripts.benchmarking.measurement import trip_input
from scripts.local_common import ROOT

DATABASE = ROOT / "var/road_nationwide/station_edges_v2_live.db"

GRAPH_VERSION = "geofabrik-us-20261009-valhalla-3.9.1-auto"


def hosted_reference(
    name: str,
    rows: list[dict],
    index: CandidateIndex,
    args: argparse.Namespace,
    reference_graph: DirectedFuelGraph | None = None,
) -> dict:
    """Independent offline search on the actual same-profile online matrix.

    This strengthens comparisons without claiming omitted national offers
    cannot improve the answer. Verified identical sequences can be reused.
    """
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
    trip = trip_input(BENCHMARK_CASES[name])
    start, finish = resolve_inputs(trip)
    scope = screen_csv_offers(
        index.stations,
        start.coordinates,
        finish.coordinates,
        trip.initial_fuel_gallons,
        trip.reserve_gallons,
        Decimal(incumbent["purchase_cost"]),
        price_access_bound=True,
    )
    result = {
        "case": name,
        "us_csv_offers": len(index.stations),
        "safely_excluded_offers": scope.excluded_by_cost,
        "possible_offers": len(scope.possible_offers),
        "reference_cost": incumbent["purchase_cost"],
        "reference_kind": "best_observed_verified",
        "independent_reference_available": False,
        "zero_purchase_case": Decimal(incumbent["purchase_cost"]) == 0,
        "evidence_scope": "captured hosted candidate graph",
        "offline_hosted_attempts": 0,
    }
    if result["zero_purchase_case"]:
        result["status"] = "zero_purchase_minimum"
        return result
    graph = reference_graph or HOSTED_GRAPHS.get(name)
    if (
        graph is None
        or graph.durations_seconds is None
        or graph.durations_seconds[0][-1] is None
    ):
        result["status"] = "no_captured_hosted_graph"
        return result
    result["hosted_matrix_offers"] = len(graph.stations)
    result["graph_version"] = graph.version
    started = perf_counter()
    budget = RequestBudget(deadline_seconds=args.reference_seconds)
    limit = graph.durations_seconds[0][-1] + 1800
    search = search_fuel_graph(
        graph,
        trip.initial_fuel_gallons,
        trip.reserve_gallons,
        max_total_seconds=limit,
        max_expansions=500_000,
        seed_station_ids=tuple(incumbent["station_ids"]),
        stop_when=lambda: budget.remaining_seconds <= 1,
    )
    mip = search_fuel_graph_mip(
        graph,
        trip.initial_fuel_gallons,
        trip.reserve_gallons,
        max_total_seconds=limit,
        max_solver_seconds=args.mip_seconds,
    )
    result.update(
        search_complete=search.complete,
        search_expansions=search.expansions,
        independent_solver_complete=mip.complete,
        search_and_solver_seconds=round(perf_counter() - started, 3),
    )
    candidates = []
    verified_by_ids = {
        tuple(row["station_ids"]): row
        for row in successes
        if row.get("routed_station_count") == len(row["station_ids"])
    }
    by_id = {station.station_id: station for station in index.stations}
    adapter = OpenRouteServiceAdapter(os.environ["ORS_API_KEY"])
    verify_budget = RequestBudget(deadline_seconds=30)
    direct = None
    try:
        for source, solution in (
            ("extended_same_profile_search", search.solution),
            ("independent_same_profile_mip", mip.solution),
        ):
            if solution is None:
                continue
            observed = verified_by_ids.get(solution.station_ids)
            prior = next(
                (
                    row
                    for row in candidates
                    if row.get("valid")
                    and row.get("station_ids") == list(solution.station_ids)
                ),
                None,
            )
            try:
                if observed or prior:
                    cost = Decimal(
                        observed["purchase_cost"] if observed else prior["cost"]
                    )
                    candidate = {
                        "source": source,
                        "valid": True,
                        "cost": str(cost),
                        "station_ids": list(solution.station_ids),
                        "reuses_hosted_verification": True,
                    }
                else:
                    if direct is None:
                        direct = adapter.route(
                            (start.coordinates, finish.coordinates), verify_budget
                        )
                    stations = tuple(
                        by_id[station_id] for station_id in solution.station_ids
                    )
                    route = adapter.route(
                        (
                            start.coordinates,
                            *(station.coordinates for station in stations),
                            finish.coordinates,
                        ),
                        verify_budget,
                    )
                    if route.duration_seconds > direct.duration_seconds + 1800:
                        raise ValueError("hosted_duration_limit")
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
                        raise ValueError("snap_limit")
                    fuel = calculate_fuel(
                        stations,
                        tuple(leg.distance_meters for leg in route.legs),
                        trip.initial_fuel_gallons,
                        trip.reserve_gallons,
                    )
                    cost = usd_cents(fuel.exact_purchase_cost)
                    candidate = {
                        "source": source,
                        "valid": True,
                        "cost": str(cost),
                        "station_ids": list(solution.station_ids),
                    }
                candidates.append(candidate)
                if source == "independent_same_profile_mip":
                    result["independent_reference_available"] = True
                if cost < Decimal(result["reference_cost"]):
                    result["reference_cost"], result["reference_kind"] = (
                        str(cost),
                        source + "_hosted_verified",
                    )
            except Exception as error:
                candidates.append(
                    {"source": source, "valid": False, "reason": type(error).__name__}
                )
    finally:
        adapter.close()
        result["offline_hosted_attempts"] = verify_budget.attempts
    result["verified_offline_candidates"] = candidates
    result["status"] = "best_known_comparison_only"
    result["offline_seconds"] = round(perf_counter() - started, 3)
    return result
