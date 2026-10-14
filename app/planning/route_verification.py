"""Road waypoint checks and fuel reconstruction for selected stops."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING

from app.domain.fuel import FuelPlan
from app.domain.routing import Coordinates, Route
from app.domain.units import METERS_PER_MILE
from app.planning.budget import RequestBudget
from app.planning.candidates import ProjectedCandidate
from app.planning.corridor_selection import choose_corridor, compact_corridor
from app.planning.fuel import FuelShortfall
from app.planning.trip_results import calculate_fuel, finalize_trip
from app.planning.trip_types import (
    DetourLimitExceeded,
    DurationLimitExceeded,
    NoFeasiblePlan,
    PlannedTrip,
    RouteCheckFailed,
)
from app.planning.verification_timing import verification_timing

if TYPE_CHECKING:
    from app.planning.trip_context import PlanningContext


def verify_selection(
    context: PlanningContext,
    baseline: Route,
    selected: tuple[ProjectedCandidate, ...],
    initial: Decimal,
    reserve: Decimal,
    max_detour_miles: Decimal | None,
    max_detour_ratio: Decimal | None,
    budget: RequestBudget,
    max_total_seconds: Decimal | None,
    *,
    allow_repair: bool = True,
    route_query: Callable[[tuple[Coordinates, ...], RequestBudget], Route]
    | None = None,
) -> tuple[Route, FuelPlan, tuple[ProjectedCandidate, ...]]:
    from app.routing.client import NoDrivableRoute, RoutingRequestRejected
    from app.routing.local_valhalla import LocalValhallaUnavailable

    for attempt in range(2 if allow_repair else 1):
        bad = []
        waypoints = (
            (baseline.waypoints[0].requested,)
            + tuple(item.station.coordinates for item in selected)
            + (baseline.waypoints[-1].requested,)
        )
        try:
            began = budget.clock()
            with budget.stage("selected_directions"):
                route = (route_query or context.router.route)(waypoints, budget)
            verification_timing.observe(
                context.verification_profile, len(waypoints), budget.clock() - began
            )
            bad = [
                index
                for index, (item, waypoint) in enumerate(
                    zip(selected, route.waypoints[1:-1], strict=True)
                )
                if waypoint.snap_distance_meters
                > (
                    Decimal(1000)
                    if item.station.location_precision == "city_representative"
                    else Decimal(250)
                )
            ]
            if bad:
                raise RouteCheckFailed(f"selected station snap failed at {bad[0]}")
            extra = max(Decimal(0), route.distance_meters - baseline.distance_meters)
            if (
                max_detour_miles is not None
                and extra > max_detour_miles * METERS_PER_MILE
            ) or (
                max_detour_ratio is not None
                and extra > baseline.distance_meters * max_detour_ratio
            ):
                raise DetourLimitExceeded("verified route exceeds detour policy")
            if (
                max_total_seconds is not None
                and route.duration_seconds > max_total_seconds
            ):
                raise DurationLimitExceeded("verified route exceeds travel time limit")
            with budget.stage("fuel_reconstruction"):
                fuel = calculate_fuel(
                    tuple(item.station for item in selected),
                    tuple(leg.distance_meters for leg in route.legs),
                    initial,
                    reserve,
                )
            return route, fuel, selected
        except (
            NoDrivableRoute,
            RoutingRequestRejected,
            LocalValhallaUnavailable,
            FuelShortfall,
            RouteCheckFailed,
            DetourLimitExceeded,
            DurationLimitExceeded,
        ) as error:
            budget.increment_metric(
                f"{context.verification_profile}_route_check_{type(error).__name__}", 1
            )
            if not allow_repair and selected and route_query is None:
                excluded = (
                    selected[bad[0]].station.station_id
                    if isinstance(error, RouteCheckFailed) and bad
                    else max(selected, key=lambda v: v.offset_meters).station.station_id
                )
                context.rejected.add(excluded)
            if attempt or not allow_repair or budget.attempts >= budget.max_attempts:
                if isinstance(error, (DetourLimitExceeded, DurationLimitExceeded)):
                    raise
                raise RouteCheckFailed(
                    "selected route could not be verified"
                ) from error
            budget.consume_correction()
            # A failed waypoint is excluded and the baseline fuel chain is
            # recomputed before the only permitted correction route call.
            if isinstance(error, RouteCheckFailed) and bad:
                excluded = selected[bad[0]].station.station_id
            else:
                excluded = max(
                    selected, key=lambda item: item.offset_meters
                ).station.station_id
            replacement: tuple[ProjectedCandidate, ...] = ()
            if isinstance(error, (DetourLimitExceeded, DurationLimitExceeded)):
                # A price-oriented corridor can be too slow after a matrix
                # outage. Try a compact low-offset chain before spending the
                # single correction on another price-oriented sequence.
                nearby = tuple(
                    item
                    for item in context.along_route(baseline, 8_000)
                    if item.station.station_id != excluded
                )
                compact = compact_corridor(baseline, nearby, initial, reserve)
                if compact != selected:
                    replacement = compact
            if not replacement:
                replacement = choose_corridor(
                    context, baseline, initial, reserve, frozenset({excluded})
                )
            if not replacement or replacement == selected:
                if isinstance(error, (DetourLimitExceeded, DurationLimitExceeded)):
                    raise
                raise NoFeasiblePlan("no verified alternative in corridor") from error
            selected = replacement
    raise RouteCheckFailed("verification attempts exhausted")


def verify_cross_profile(
    context: PlanningContext,
    hosted: PlannedTrip,
    local: PlannedTrip,
    initial: Decimal,
    reserve: Decimal,
    max_detour_miles: Decimal | None,
    max_detour_ratio: Decimal | None,
    max_extra_duration_minutes: Decimal,
    budget: RequestBudget,
) -> PlannedTrip:
    """Apply the same hosted route and fuel checks to a local stop chain."""
    progress = Decimal(0)
    proposal: list[ProjectedCandidate] = []
    for index, station in enumerate(local.routed_stations):
        progress += local.verified.legs[index].distance_meters
        proposal.append(
            ProjectedCandidate(
                station, progress, Decimal(0), f"{station.station_id}:portfolio"
            )
        )
    verified, fuel, selected = verify_selection(
        context,
        hosted.baseline,
        tuple(proposal),
        initial,
        reserve,
        max_detour_miles,
        max_detour_ratio,
        budget,
        hosted.baseline.duration_seconds + max_extra_duration_minutes * 60,
    )
    return finalize_trip(
        hosted.baseline,
        verified,
        selected,
        fuel,
        initial,
        hosted.initial_price,
        hosted.initial_price_basis,
        hosted.candidate_version,
        budget,
        "portfolio_local_hosted_verified",
    )
