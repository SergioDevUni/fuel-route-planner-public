"""Exact final-plan ordering shared by search and verified selection."""

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.fuel import FuelPlan
    from app.domain.routing import Route
    from app.planning.candidates import ProjectedCandidate
    from app.planning.trip_types import PlannedTrip

PlanRank = tuple[Decimal, Decimal, int, tuple[str, ...]]


def solution_rank(cost: Decimal, meters: Decimal, ids: tuple[str, ...]) -> PlanRank:
    return cost, meters, len(ids), ids


def rank_plan(plan: "PlannedTrip") -> PlanRank:
    return solution_rank(
        plan.fuel.exact_purchase_cost,
        plan.verified.distance_meters,
        tuple(station.station_id for station in plan.routed_stations),
    )


def rank_selection(
    route: "Route", fuel: "FuelPlan", selected: "tuple[ProjectedCandidate, ...]"
) -> PlanRank:
    return solution_rank(
        fuel.exact_purchase_cost,
        route.distance_meters,
        tuple(item.station.station_id for item in selected),
    )
