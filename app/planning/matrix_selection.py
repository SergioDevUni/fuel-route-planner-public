"""Directed matrix search and fuel-aware proposal ranking."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from app.domain.fuel import VehicleSpec
from app.domain.ledger import usd_cents
from app.domain.routing import Coordinates, Route
from app.domain.units import METERS_PER_MILE
from app.planning.budget import RequestBudget
from app.planning.candidates import ProjectedCandidate, discovery_context
from app.planning.fuel_graph import GraphFuelSolution, GraphSearchResult
from app.planning.graph_search import search_fuel_graph
from app.planning.matrix_candidates import matrix_points, select_matrix_candidates
from app.planning.road_graph import graph_from_matrix
from app.planning.trip_policy import extra_distance
from app.planning.verification_timing import verification_timing
from app.routing.client import RoutingUnavailable

if TYPE_CHECKING:
    from app.planning.trip_context import PlanningContext

logger = logging.getLogger("app.planning.trip")


def rank_verification_candidate(
    solution: GraphFuelSolution, initial: Decimal, reserve: Decimal
) -> tuple[Decimal, Decimal, int, Decimal, tuple[str, ...]]:
    vehicle = VehicleSpec()
    first_range = (initial - reserve) * vehicle.miles_per_gallon * METERS_PER_MILE
    later_range = (
        (vehicle.capacity_gallons - reserve)
        * vehicle.miles_per_gallon
        * METERS_PER_MILE
    )
    utilization = max(
        (
            distance / (first_range if i == 0 else later_range)
            if (first_range if i == 0 else later_range) > 0
            else Decimal(0)
            if distance == 0
            else Decimal("Infinity")
            for i, distance in enumerate(solution.leg_distances_meters)
        ),
        default=Decimal(0),
    )
    # The exact search stays unchanged. Among observed proposals with the same
    # displayed cash spending, prefer fuel slack before extra waypoints or
    # last-digit savings that may disappear in final road verification.
    return (
        usd_cents(solution.purchase_cost),
        utilization,
        len(solution.station_ids),
        solution.purchase_cost,
        solution.station_ids,
    )


def select_matrix(
    context: PlanningContext,
    baseline: Route,
    provisional: tuple[ProjectedCandidate, ...],
    initial: Decimal,
    reserve: Decimal,
    max_detour_miles: Decimal | None,
    max_detour_ratio: Decimal | None,
    budget: RequestBudget,
    max_total_seconds: Decimal | None,
) -> tuple[tuple[ProjectedCandidate, ...], bool] | None:
    """Discover bounded offers, search measured roads, and rank verification."""
    from app.routing.client import NoDrivableRoute, RoutingRequestRejected
    from app.routing.local_valhalla import LocalValhallaUnavailable
    from app.routing.normalizer import RoutingResponseInvalid

    if context.matrix_provider is None:
        return None
    if context.incumbent is not None and budget.target_remaining_seconds <= (
        verification_timing.reserve(context.verification_profile, len(provisional) + 2)
    ):
        budget.increment_metric("optional_matrix_skipped_for_deadline", 1)
        return None
    discovery = _discover_matrix(
        context,
        baseline,
        provisional,
        initial,
        reserve,
        max_detour_miles,
        max_detour_ratio,
        budget,
        max_total_seconds,
    )
    if discovery is None:
        return None
    try:
        result, verification_candidate = _search_matrix(
            context,
            baseline,
            provisional,
            initial,
            reserve,
            budget,
            max_total_seconds,
            discovery,
        )
    except (
        NoDrivableRoute,
        RoutingRequestRejected,
        RoutingUnavailable,
        RoutingResponseInvalid,
        LocalValhallaUnavailable,
        ValueError,
    ) as error:
        logger.info(
            "matrix_selection_fallback reason=%s offers=%d points=%d",
            type(error).__name__,
            len(discovery.chosen),
            len(discovery.points),
        )
        return None
    return _select_proposal(
        context, initial, reserve, budget, discovery, result, verification_candidate
    )


@dataclass(frozen=True)
class MatrixDiscovery:
    chosen: tuple[ProjectedCandidate, ...]
    off_route_count: int
    max_extra: Decimal | None
    points: tuple[Coordinates, ...]
    offer_indices: tuple[int, ...]


def _discover_matrix(
    context: PlanningContext,
    baseline: Route,
    provisional: tuple[ProjectedCandidate, ...],
    initial: Decimal,
    reserve: Decimal,
    max_detour_miles: Decimal | None,
    max_detour_ratio: Decimal | None,
    budget: RequestBudget,
    max_total_seconds: Decimal | None,
) -> MatrixDiscovery | None:
    with (
        budget.stage("station_discovery"),
        discovery_context(budget, context.verification_profile),
    ):
        distance_limit = extra_distance(baseline, max_detour_miles, max_detour_ratio)
        max_extra = distance_limit
        # The broad time-only discovery envelope is a heuristic, not a route
        # acceptance limit. The matrix and final directions enforce duration.
        discovery_total = (
            baseline.distance_meters + max_extra
            if max_extra is not None
            else max_total_seconds * 100
            if max_total_seconds is not None
            else baseline.distance_meters * 2
        )
        radius = min(
            80_000.0,
            max(
                32_000.0,
                float(max_extra) * 2 if max_extra is not None else 80_000.0,
            ),
        )
        along = context.along_route(baseline, radius)
        nearby_ids = {item.station.station_id for item in along}
        envelope = context.candidates.within_detour_envelope(
            baseline, max(discovery_total, baseline.distance_meters)
        )
        off_route = tuple(
            item for item in envelope if item.station.station_id not in nearby_ids
        )
        alternatives = tuple(
            v for v in along + off_route if v.station.station_id not in context.rejected
        )
        budget.audit_offers(
            f"{context.verification_profile}_discovered",
            (v.station.station_id for v in alternatives),
        )
        budget.audit_offers(
            f"{context.verification_profile}_rejected", iter(context.rejected)
        )
        chosen = select_matrix_candidates(
            provisional,
            alternatives,
            baseline.distance_meters,
            offset_price_penalty_per_mile=(
                budget.optimization.access_price_penalty
                if max_extra is None
                else Decimal(0)
            ),
            start=baseline.waypoints[0].requested,
            initial_gallons=initial,
            reserve_gallons=reserve,
        )
        if not chosen:
            logger.info("matrix_selection_fallback reason=no_candidates")
            return None
        budget.audit_offers(
            f"{context.verification_profile}_selected",
            (item.station.station_id for item in chosen),
        )
        points, offer_indices = matrix_points(
            baseline.waypoints[0].requested,
            chosen,
            baseline.waypoints[-1].requested,
        )
    return MatrixDiscovery(chosen, len(off_route), max_extra, points, offer_indices)


def _search_matrix(
    context: PlanningContext,
    baseline: Route,
    provisional: tuple[ProjectedCandidate, ...],
    initial: Decimal,
    reserve: Decimal,
    budget: RequestBudget,
    max_total_seconds: Decimal | None,
    discovery: MatrixDiscovery,
) -> tuple[GraphSearchResult, GraphFuelSolution | None]:
    with budget.stage("matrix_acquisition"):
        assert context.matrix_provider is not None
        matrix = context.matrix_provider.matrix(discovery.points, budget)
    graph = graph_from_matrix(
        tuple(item.station for item in discovery.chosen),
        matrix,
        f"directed-matrix:{context.candidates.version}",
        station_point_indices=discovery.offer_indices,
    )
    if max_total_seconds is not None and graph.durations_seconds is None:
        raise ValueError("matrix duration data is missing")
    graph_time_limit = max_total_seconds
    if max_total_seconds is not None:
        assert graph.durations_seconds is not None
        matrix_baseline_seconds = graph.durations_seconds[0][-1]
        if matrix_baseline_seconds is None:
            raise ValueError("matrix baseline duration is missing")
        graph_time_limit = matrix_baseline_seconds + (
            max_total_seconds - baseline.duration_seconds
        )
    verification_points = len(provisional) + 2
    verification_candidate: GraphFuelSolution | None = None

    def improvement(solution: GraphFuelSolution) -> None:
        nonlocal verification_points, verification_candidate
        if verification_candidate is None or rank_verification_candidate(
            solution, initial, reserve
        ) < rank_verification_candidate(verification_candidate, initial, reserve):
            verification_candidate = solution
        verification_points = len(verification_candidate.station_ids) + 2
        budget.record_candidate(
            solution.purchase_cost, profile=context.verification_profile
        )

    with budget.stage("fuel_search"):
        result = search_fuel_graph(
            graph,
            initial,
            reserve,
            max_total_meters=(
                baseline.distance_meters + discovery.max_extra
                if discovery.max_extra is not None
                else None
            ),
            max_total_seconds=graph_time_limit,
            max_expansions=100_000,
            # Once a feasible graph proposal exists, leave directions inside
            # the preferred planning window even before a verified snapshot.
            # Discovery without a proposal retains the hard recovery window.
            stop_when=lambda: budget.cancelled
            or (
                budget.target_remaining_seconds
                if verification_candidate is not None
                or budget.first_verified_seconds is not None
                else budget.planning_remaining_seconds
            )
            <= (
                verification_timing.reserve(
                    context.verification_profile, verification_points
                )
            ),
            seed_station_ids=tuple(item.station.station_id for item in provisional),
            on_improvement=improvement,
        )

    return result, verification_candidate


def _select_proposal(
    context: PlanningContext,
    initial: Decimal,
    reserve: Decimal,
    budget: RequestBudget,
    discovery: MatrixDiscovery,
    result: GraphSearchResult,
    verification_candidate: GraphFuelSolution | None,
) -> tuple[tuple[ProjectedCandidate, ...], bool] | None:
    by_id = {item.station.station_id: item for item in discovery.chosen}
    budget.record_search(
        scope=f"{context.verification_profile}_bounded_directed_matrix",
        offers=len(discovery.chosen),
        complete=result.complete,
        expansions=result.expansions,
    )
    logger.info(
        "matrix_selection offers=%d points=%d cells=%d off_route_candidates=%d "
        "expansions=%d "
        "complete=%s selected=%d labels_pruned=%d",
        len(discovery.chosen),
        len(discovery.points),
        len(discovery.points) ** 2,
        discovery.off_route_count,
        result.expansions,
        result.complete,
        len(result.solution.station_ids) if result.solution else 0,
        result.labels_pruned,
    )
    if result.solution is None or not result.solution.station_ids:
        return None
    chosen_solution = result.solution
    if (
        (not budget.optimization.use_full_window)
        and verification_candidate is not None
        and rank_verification_candidate(verification_candidate, initial, reserve)
        < rank_verification_candidate(chosen_solution, initial, reserve)
    ):
        chosen_solution = verification_candidate
        budget.increment_metric(
            f"{context.verification_profile}_cash_tie_verification_candidates", 1
        )
    if context.incumbent is not None and budget.target_remaining_seconds <= (
        verification_timing.reserve(
            context.verification_profile, len(chosen_solution.station_ids) + 2
        )
    ):
        budget.increment_metric("optional_verification_skipped_for_deadline", 1)
        return None
    return (
        tuple(by_id[station_id] for station_id in chosen_solution.station_ids),
        result.complete and chosen_solution == result.solution,
    )
