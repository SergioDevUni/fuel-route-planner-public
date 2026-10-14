"""Recover an pending_check station sequence through the road refiner."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.domain.fuel import FuelPlan
from app.domain.routing import Route
from app.planning.budget import RequestBudget
from app.planning.candidates import ProjectedCandidate
from app.planning.route_verification import verify_selection
from app.planning.trip_policy import maximum_distance
from app.planning.trip_types import (
    DetourLimitExceeded,
    DurationLimitExceeded,
    NoFeasiblePlan,
    RouteCheckFailed,
)

if TYPE_CHECKING:
    from app.planning.trip_context import PlanningContext


def verify_or_recover(
    context: PlanningContext,
    baseline: Route,
    selected: tuple[ProjectedCandidate, ...],
    initial: Decimal,
    reserve: Decimal,
    max_detour_miles: Decimal | None,
    max_detour_ratio: Decimal | None,
    budget: RequestBudget,
    max_total_seconds: Decimal,
) -> tuple[Route, FuelPlan, tuple[ProjectedCandidate, ...], bool]:
    try:
        if not selected:
            raise NoFeasiblePlan("no fuel chain in the configured search region")
        route, fuel, selected = verify_selection(
            context,
            baseline,
            selected,
            initial,
            reserve,
            max_detour_miles,
            max_detour_ratio,
            budget,
            max_total_seconds,
        )
        return route, fuel, selected, False
    except (
        NoFeasiblePlan,
        RouteCheckFailed,
        DetourLimitExceeded,
        DurationLimitExceeded,
    ) as original:
        refiner = context.national_refiner
        if (
            refiner is None
            or budget.attempts >= budget.max_attempts
            or budget.remaining_seconds <= 7
        ):
            raise
        distance_limit = maximum_distance(baseline, max_detour_miles, max_detour_ratio)
        proposal = refiner.propose(
            baseline.waypoints[0].requested,
            baseline.waypoints[-1].requested,
            initial,
            reserve,
            None,
            max_total_seconds,
            distance_limit,
            budget,
        )
        if not proposal or tuple(item.station.station_id for item in proposal) == tuple(
            item.station.station_id for item in selected
        ):
            raise original
        try:
            route, fuel, selected = verify_selection(
                context,
                baseline,
                proposal,
                initial,
                reserve,
                max_detour_miles,
                max_detour_ratio,
                budget,
                max_total_seconds,
            )
        except (
            RouteCheckFailed,
            DetourLimitExceeded,
            DurationLimitExceeded,
        ):
            raise original from None
        return route, fuel, selected, True
