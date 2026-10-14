"""Build a verified road-first incumbent before matrix improvement."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from app.domain.fuel import VehicleSpec
from app.domain.routing import Coordinates, Route
from app.planning.budget import RequestBudget
from app.planning.candidates import ProjectedCandidate
from app.planning.corridor_selection import choose_corridor, compact_corridor
from app.planning.initial_route import initial_corridor
from app.planning.matrix_selection import select_matrix
from app.planning.route_verification import verify_selection
from app.planning.trip_types import (
    DetourLimitExceeded,
    DurationLimitExceeded,
    NoFeasiblePlan,
    RouteCheckFailed,
)
from app.routing.client import RoutingUnavailable

if TYPE_CHECKING:
    from app.planning.trip_context import PlanningContext

logger = logging.getLogger("app.planning.trip")


def select_matrix_first(
    context: PlanningContext,
    start: Coordinates,
    finish: Coordinates,
    initial: Decimal,
    reserve: Decimal,
    max_detour_miles: Decimal | None,
    max_detour_ratio: Decimal | None,
    max_extra_duration_minutes: Decimal,
    optimization_mode: str,
    budget: RequestBudget,
    price: Decimal | None,
    basis: str,
) -> tuple[Route, tuple[ProjectedCandidate, ...], bool, str] | None:
    """Select station candidates from road geometry and seed directed search.

    One baseline, one matrix and one verification normally
    leave the fourth hosted attempt for waypoint recovery.
    """
    baseline = context.baseline(start, finish, budget)
    if VehicleSpec().gallons_for_meters(baseline.distance_meters) + reserve <= initial:
        return baseline, (), True, "road_first_no_stop"
    with budget.stage("incumbent_discovery"):
        provisional = choose_corridor(context, baseline, initial, reserve)
    if len(provisional) > 12:
        with budget.stage("compact_incumbent_discovery"):
            compact = compact_corridor(
                baseline,
                context.along_route(baseline, 8_000),
                initial,
                reserve,
            )
            if not compact:
                compact = compact_corridor(
                    baseline,
                    context.along_route(baseline, 32_000),
                    initial,
                    reserve,
                )
        provisional = (
            initial_corridor(
                baseline,
                context.along_route(baseline, 500),
                compact,
                initial,
                reserve,
                budget,
            )
            or provisional
        )
    # Verify a feasible sequence before expensive improvement. An optional
    # verification failure leaves two calls for matrix + selected directions.
    if provisional and budget.attempts <= 1 and budget.remaining_seconds > 5:
        try:
            with budget.stage("incumbent_verification"):
                route, fuel, provisional = verify_selection(
                    context,
                    baseline,
                    provisional,
                    initial,
                    reserve,
                    max_detour_miles,
                    max_detour_ratio,
                    budget,
                    baseline.duration_seconds + max_extra_duration_minutes * 60,
                    allow_repair=False,
                )
            context.checkpoint(
                baseline,
                route,
                fuel,
                provisional,
                initial,
                price,
                basis,
                budget,
                "road_first_verified_incumbent",
            )
        except (
            NoFeasiblePlan,
            RouteCheckFailed,
            DetourLimitExceeded,
            DurationLimitExceeded,
            RoutingUnavailable,
        ) as error:
            logger.info("early_incumbent_unavailable reason=%s", type(error).__name__)
            replacement: tuple[ProjectedCandidate, ...] = ()
            if isinstance(error, (DetourLimitExceeded, DurationLimitExceeded)):
                replacement = compact_corridor(
                    baseline,
                    tuple(
                        v
                        for v in context.along_route(baseline, 8_000)
                        if v.station.station_id not in context.rejected
                    ),
                    initial,
                    reserve,
                )
            if not replacement:
                replacement = choose_corridor(
                    context, baseline, initial, reserve, frozenset(context.rejected)
                )
            if replacement and replacement != provisional:
                budget.consume_correction()
                provisional = replacement
    result = select_matrix(
        context,
        baseline,
        provisional,
        initial,
        reserve,
        max_detour_miles,
        max_detour_ratio,
        budget,
        baseline.duration_seconds + max_extra_duration_minutes * 60,
    )
    if result is not None:
        selected, complete = result
        return baseline, selected, complete, "road_first_directed_graph"
    return baseline, provisional, False, "road_corridor_recovery"
