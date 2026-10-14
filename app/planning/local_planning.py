"""Local road strategy and verified optional route improvements."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from app.domain.fuel import FuelPlan, VehicleSpec
from app.domain.routing import Route
from app.planning.baseline_cache import AlternativeRouteProvider
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget
from app.planning.candidates import ProjectedCandidate
from app.planning.corridor_selection import choose_corridor, compact_corridor
from app.planning.fuel import FuelShortfall
from app.planning.ranking import rank_selection
from app.planning.request import PlanningRequest
from app.planning.trip_policy import maximum_distance
from app.planning.trip_results import calculate_fuel, starting_fuel_price
from app.planning.trip_types import (
    DetourLimitExceeded,
    DurationLimitExceeded,
    NoFeasiblePlan,
    PlannedTrip,
    RouteCheckFailed,
)
from app.routing.client import RoutingUnavailable

if TYPE_CHECKING:
    from app.planning.trip_context import PlanningContext

logger = logging.getLogger("app.planning.trip")


def plan_local_first(
    context: PlanningContext,
    request: PlanningRequest,
    *,
    budget: RequestBudget | None = None,
) -> PlannedTrip:
    budget = budget or RequestBudget()
    budget.configure_mode(request.optimization_mode)
    return LocalStages(
        context=context,
        request=request,
        budget=budget,
    ).run()


@dataclass
class LocalStages:
    context: PlanningContext
    request: PlanningRequest
    budget: RequestBudget
    price: Decimal | None = field(init=False)
    basis: str = field(init=False)
    baseline: Route = field(init=False)
    max_total_seconds: Decimal = field(init=False)
    selected: tuple[ProjectedCandidate, ...] = field(init=False)
    verified: Route = field(init=False)
    fuel: FuelPlan = field(init=False)
    method: str = field(init=False)
    incumbent_route: Route | None = field(init=False)
    incumbent_fuel: FuelPlan | None = field(init=False)

    def run(self) -> PlannedTrip:
        """Select a local route, improve it and construct the verified result."""
        if self.context.national_refiner is None:
            raise ValueError("local-first planning needs a road graph")
        self.context.discovery_budget = self.budget
        self.budget.check_deadline()
        self.price, self.basis = starting_fuel_price(
            self.request.initial_price,
            self.request.initial,
            self.request.start,
            self.context.candidates.stations,
        )
        self.baseline = self.context.router.route(
            (self.request.start, self.request.finish), self.budget
        )
        logger.info(
            "local_first_baseline distance_meters=%s remaining=%.2f",
            self.baseline.distance_meters,
            self.budget.remaining_seconds,
        )
        self.max_total_seconds = (
            self.baseline.duration_seconds
            + self.request.max_extra_duration_minutes * 60
        )
        if (
            VehicleSpec().gallons_for_meters(self.baseline.distance_meters)
            + self.request.reserve
            <= self.request.initial
        ):
            self.selected: tuple[ProjectedCandidate, ...] = ()
            self.verified = self.baseline
            self.fuel = calculate_fuel(
                (),
                (self.baseline.distance_meters,),
                self.request.initial,
                self.request.reserve,
            )
            self.method = "local_road_no_stop"
        else:
            self._corridor()
            self._proposal()
            if self.incumbent_route is None or self.incumbent_fuel is None:
                raise NoFeasiblePlan("no verified plan in completed local road scope")
            self.verified, self.fuel = (self.incumbent_route, self.incumbent_fuel)
        self._shorter_route()
        return self.context.complete(
            self.request,
            self.baseline,
            self.verified,
            self.selected,
            self.fuel,
            self.price,
            self.basis,
            self.budget,
            self.method,
        )

    def _corridor(self) -> None:
        """Try a compact corridor to seed a safe cost upper bound."""
        self.selected = ()
        self.incumbent_route: Route | None = None
        self.incumbent_fuel: FuelPlan | None = None
        corridor = choose_corridor(
            self.context, self.baseline, self.request.initial, self.request.reserve
        )
        logger.info(
            "local_first_corridor candidates=%d remaining=%.2f",
            len(corridor),
            self.budget.remaining_seconds,
        )
        if corridor and len(corridor) <= 12:
            try:
                self.incumbent_route, self.incumbent_fuel, self.selected = (
                    self.context.verify(
                        self.request, self.baseline, corridor, self.budget
                    )
                )
            except (
                NoFeasiblePlan,
                RouteCheckFailed,
                DetourLimitExceeded,
                DurationLimitExceeded,
            ) as error:
                logger.info(
                    "local_first_corridor_failed reason=%s remaining=%.2f",
                    type(error).__name__,
                    self.budget.remaining_seconds,
                )
                self.selected = ()
            else:
                logger.info(
                    "local_first_corridor_verified remaining=%.2f",
                    self.budget.remaining_seconds,
                )
        elif corridor:
            logger.info(
                "local_first_corridor_deferred stops=%d remaining=%.2f",
                len(corridor),
                self.budget.remaining_seconds,
            )
            compact = compact_corridor(
                self.baseline,
                self.context.along_route(self.baseline, 32000),
                self.request.initial,
                self.request.reserve,
            )
            if compact:
                try:
                    self.incumbent_route, self.incumbent_fuel, self.selected = (
                        self.context.verify(
                            self.request, self.baseline, compact, self.budget
                        )
                    )
                except (
                    NoFeasiblePlan,
                    RouteCheckFailed,
                    DetourLimitExceeded,
                    DurationLimitExceeded,
                ) as error:
                    logger.info(
                        "local_first_compact_failed stops=%d reason=%s",
                        len(compact),
                        type(error).__name__,
                    )
                    self.selected = ()
                else:
                    logger.info("local_first_compact_verified stops=%d", len(compact))
        self.method = "local_road_corridor_first"

    def _proposal(self) -> None:
        """Search completed road sources while retaining the verified incumbent."""
        assert self.context.national_refiner is not None
        if self.incumbent_route is not None and self.incumbent_fuel is not None:
            self.context.checkpoint(
                self.baseline,
                self.incumbent_route,
                self.incumbent_fuel,
                self.selected,
                self.request.initial,
                self.price,
                self.basis,
                self.budget,
                "local_road_verified_incumbent",
            )
        distance_limit = maximum_distance(
            self.baseline, self.request.max_detour_miles, self.request.max_detour_ratio
        )
        proposal_budget = (
            self.budget.child(min(7.5, self.budget.remaining_seconds - 2))
            if self.incumbent_fuel is not None and self.budget.remaining_seconds > 2
            else self.budget
        )
        try:
            proposal = self.context.national_refiner.propose(
                self.request.start,
                self.request.finish,
                self.request.initial,
                self.request.reserve,
                self.incumbent_fuel.exact_purchase_cost
                if self.incumbent_fuel
                else None,
                self.max_total_seconds,
                distance_limit,
                proposal_budget,
                baseline=self.baseline,
                seed_ids=tuple(v.station.station_id for v in self.selected),
                incumbent_route=self.incumbent_route,
            )
        except PlanningDeadlineExceeded:
            if self.incumbent_fuel is None:
                raise
            logger.info("local_first_refiner_deadline_kept_incumbent")
            proposal = None
        logger.info(
            "local_first_refiner proposal=%d remaining=%.2f",
            len(proposal) if proposal else 0,
            self.budget.remaining_seconds,
        )
        if proposal:
            try:
                route, proposal_fuel, proposal_selected = self.context.verify(
                    self.request, self.baseline, proposal, self.budget
                )
            except (
                NoFeasiblePlan,
                RouteCheckFailed,
                DetourLimitExceeded,
                DurationLimitExceeded,
            ):
                if self.incumbent_route is None:
                    raise
            else:
                if (
                    self.incumbent_fuel is None
                    or self.incumbent_route is None
                    or rank_selection(route, proposal_fuel, proposal_selected)
                    < rank_selection(
                        self.incumbent_route, self.incumbent_fuel, self.selected
                    )
                ):
                    self.incumbent_route = route
                    self.incumbent_fuel = proposal_fuel
                    self.selected = proposal_selected
                    self.method = "csv_road_first"

    def _shorter_route(self) -> None:
        """Verify optional shorter geometry on the same local road profile."""
        if (
            self.selected
            and getattr(self.context.router, "supports_shorter_routes", False)
            and (self.budget.remaining_seconds > 3)
            and (self.fuel.exact_purchase_cost > 0)
        ):
            self.context.checkpoint(
                self.baseline,
                self.verified,
                self.fuel,
                self.selected,
                self.request.initial,
                self.price,
                self.basis,
                self.budget,
                self.method,
            )
            try:
                with self.budget.stage("local_shorter_route_verification"):
                    alternative, alternative_fuel, _ = self.context.verify(
                        self.request,
                        self.baseline,
                        self.selected,
                        self.budget.child(
                            min(3.0, self.budget.remaining_seconds - 0.5)
                        ),
                        allow_repair=False,
                        route_query=cast(
                            AlternativeRouteProvider, self.context.router
                        ).shorter_route,
                    )
                if rank_selection(alternative, alternative_fuel, self.selected) < (
                    rank_selection(self.verified, self.fuel, self.selected)
                ):
                    self.verified, self.fuel = (alternative, alternative_fuel)
                    self.method = "local_road_shorter_verified"
            except (
                PlanningDeadlineExceeded,
                RoutingUnavailable,
                NoFeasiblePlan,
                RouteCheckFailed,
                FuelShortfall,
                DetourLimitExceeded,
                DurationLimitExceeded,
            ):
                pass
