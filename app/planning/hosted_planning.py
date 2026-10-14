"""Hosted trip orchestration with a retained verified incumbent."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from app.domain.fuel import FuelPlan, VehicleSpec
from app.domain.routing import Route
from app.domain.units import METERS_PER_MILE
from app.planning.budget import (
    PlanningDeadlineExceeded,
    RequestBudget,
    RoutingBudgetExceeded,
)
from app.planning.candidates import ProjectedCandidate
from app.planning.corridor_selection import choose_corridor
from app.planning.fuel import FuelShortfall
from app.planning.matrix_first import select_matrix_first
from app.planning.matrix_selection import select_matrix
from app.planning.ranking import rank_selection
from app.planning.request import PlanningRequest
from app.planning.route_recovery import verify_or_recover
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
from app.routing.geometry import geodesic_meters

if TYPE_CHECKING:
    from app.planning.trip_context import PlanningContext

logger = logging.getLogger("app.planning.trip")


def plan_hosted(
    context: PlanningContext,
    request: PlanningRequest,
    *,
    matrix_first: bool = False,
    budget: RequestBudget | None = None,
) -> PlannedTrip:
    budget = budget or RequestBudget()
    budget.configure_mode(request.optimization_mode)
    return HostedStages(
        context=context,
        request=request,
        matrix_first=matrix_first,
        budget=budget,
    ).run()


@dataclass
class HostedStages:
    context: PlanningContext
    request: PlanningRequest
    matrix_first: bool
    budget: RequestBudget
    price: Decimal | None = field(init=False)
    basis: str = field(init=False)
    preflight: Route | None = field(init=False)
    first: tuple[Route, tuple[ProjectedCandidate, ...], bool, str] | None = field(
        init=False
    )
    baseline: Route = field(init=False)
    max_total_seconds: Decimal = field(init=False)
    first_consumption: Decimal = field(init=False)
    selected: tuple[ProjectedCandidate, ...] = field(init=False)
    recovered: bool = field(init=False)
    verified: Route = field(init=False)
    fuel: FuelPlan = field(init=False)
    selection_method: str = field(init=False)

    def run(self) -> PlannedTrip:
        """Execute discovery, verification, checkpoint and optional refinement."""
        self._baseline()
        fallback = self._select_route()
        if fallback is not None:
            return fallback
        self._checkpoint()
        self._refine()
        return self.context.complete(
            self.request,
            self.baseline,
            self.verified,
            self.selected,
            self.fuel,
            self.price,
            self.basis,
            self.budget,
            self.selection_method,
            self.first[2]
            if self.first is not None
            and self.selection_method
            in (
                "road_first_directed_graph",
                "road_corridor_recovery",
                "road_first_no_stop",
            )
            else None,
        )

    def _baseline(self) -> None:
        """Acquire a coherent baseline and optional road-first selection."""
        self.context.discovery_budget = self.budget
        self.budget.check_deadline()
        self.price, self.basis = starting_fuel_price(
            self.request.initial_price,
            self.request.initial,
            self.request.start,
            self.context.candidates.stations,
        )
        usable_meters = (
            (self.request.initial - self.request.reserve)
            * VehicleSpec().miles_per_gallon
            * METERS_PER_MILE
        )
        self.preflight = (
            self.context.baseline(self.request.start, self.request.finish, self.budget)
            if self.matrix_first
            and geodesic_meters(self.request.start, self.request.finish)
            <= usable_meters * Decimal("0.8")
            else None
        )
        self.first = (
            select_matrix_first(
                self.context,
                self.request.start,
                self.request.finish,
                self.request.initial,
                self.request.reserve,
                self.request.max_detour_miles,
                self.request.max_detour_ratio,
                self.request.max_extra_duration_minutes,
                self.request.optimization_mode,
                self.budget,
                self.price,
                self.basis,
            )
            if self.matrix_first
            and self.context.matrix_provider is not None
            and (self.preflight is None)
            else None
        )
        self.baseline = (
            self.first[0]
            if self.first is not None
            else self.preflight
            or self.context.baseline(
                self.request.start, self.request.finish, self.budget
            )
        )
        if (
            self.first is not None
            and self.baseline.engine_version is not None
            and (
                self.baseline.engine_version == "ors-matrix-direct-baseline"
                or self.baseline.engine_version.startswith("valhalla-matrix:")
            )
        ):
            self.baseline = self.context.router.route(
                (self.request.start, self.request.finish), self.budget
            )
        self.max_total_seconds = (
            self.baseline.duration_seconds
            + self.request.max_extra_duration_minutes * 60
        )
        self.first_consumption = VehicleSpec().gallons_for_meters(
            self.baseline.distance_meters
        )
        self.selected: tuple[ProjectedCandidate, ...]
        self.recovered = False

    def _select_route(self) -> PlannedTrip | None:
        """Choose direct travel or a fuel-stop route; preserve failure fallback."""
        if self.first is not None and (not self.first[1]):
            self._direct_matrix_route()
        elif (
            self.first is None
            and self.first_consumption + self.request.reserve <= self.request.initial
        ):
            self.selected = ()
            self.selection_method = "no_stop"
            self.verified = self.baseline
            self.fuel = calculate_fuel(
                (),
                (self.baseline.distance_meters,),
                self.request.initial,
                self.request.reserve,
            )
        else:
            return self._fuel_route()
        return None

    def _direct_matrix_route(self) -> None:
        """Repair a matrix direct route using measured directions when needed."""
        assert self.first is not None
        self.verified = self.baseline
        try:
            self.fuel = calculate_fuel(
                (),
                (self.verified.distance_meters,),
                self.request.initial,
                self.request.reserve,
            )
            self.selected = ()
            self.selection_method = self.first[3]
        except FuelShortfall:
            self.baseline = self.verified
            self.max_total_seconds = (
                self.baseline.duration_seconds
                + self.request.max_extra_duration_minutes * 60
            )
            self.selected = choose_corridor(
                self.context, self.baseline, self.request.initial, self.request.reserve
            )
            self.verified, self.fuel, self.selected, self.recovered = verify_or_recover(
                self.context,
                self.baseline,
                self.selected,
                self.request.initial,
                self.request.reserve,
                self.request.max_detour_miles,
                self.request.max_detour_ratio,
                self.budget,
                self.max_total_seconds,
            )
            self.selection_method = (
                (
                    "csv_road_recovery"
                    if self.context.national_refiner
                    and self.context.national_refiner.allow_partial
                    else "nationwide_csv_road_recovery"
                )
                if self.recovered
                else "matrix_first_direct_shortfall_repair"
            )

    def _fuel_route(self) -> PlannedTrip | None:
        """Select corridor and directed-matrix alternatives."""
        self.selected = (
            self.first[1]
            if self.first is not None
            else choose_corridor(
                self.context, self.baseline, self.request.initial, self.request.reserve
            )
        )
        self.selection_method = (
            self.first[3] if self.first is not None else "road_corridor_selection"
        )
        if (
            self.first is None
            and self.context.matrix_provider is not None
            and (self.preflight is None)
            and (not self.matrix_first or self.budget.attempts == 1)
        ):
            graph_selection = select_matrix(
                self.context,
                self.baseline,
                self.selected,
                self.request.initial,
                self.request.reserve,
                self.request.max_detour_miles,
                self.request.max_detour_ratio,
                self.budget,
                self.max_total_seconds,
            )
            if graph_selection is not None:
                self.selected = graph_selection[0]
                self.selection_method = "bounded_directed_matrix_graph"
        return self._verify_route()

    def _verify_route(self) -> PlannedTrip | None:
        """Verify the selection or keep the existing verified incumbent."""
        if self.context.incumbent is not None and tuple(
            v.station.station_id for v in self.selected
        ) == tuple(v.station_id for v in self.context.incumbent.routed_stations):
            self.verified, self.fuel = (
                self.context.incumbent.verified,
                self.context.incumbent.fuel,
            )
        else:
            try:
                self.verified, self.fuel, self.selected, self.recovered = (
                    verify_or_recover(
                        self.context,
                        self.baseline,
                        self.selected,
                        self.request.initial,
                        self.request.reserve,
                        self.request.max_detour_miles,
                        self.request.max_detour_ratio,
                        self.budget,
                        self.max_total_seconds,
                    )
                )
            except (
                NoFeasiblePlan,
                RouteCheckFailed,
                DetourLimitExceeded,
                DurationLimitExceeded,
                RoutingUnavailable,
                RoutingBudgetExceeded,
                PlanningDeadlineExceeded,
            ) as error:
                if self.context.incumbent is None:
                    raise
                self.budget.increment_metric(
                    f"{self.context.verification_profile}_optimized_verification_{type(error).__name__}",
                    1,
                )
                self.budget.check_deadline()
                return replace(
                    self.context.incumbent, outbound_attempts=self.budget.attempts
                )
        if self.recovered:
            self.selection_method = (
                "csv_road_recovery"
                if self.context.national_refiner
                and self.context.national_refiner.allow_partial
                else "nationwide_csv_road_recovery"
            )

        return None

    def _checkpoint(self) -> None:
        """Retain the best verified snapshot before optional work."""
        retained = self.context.checkpoint(
            self.baseline,
            self.verified,
            self.fuel,
            self.selected,
            self.request.initial,
            self.price,
            self.basis,
            self.budget,
            self.selection_method,
        )
        self.verified, self.fuel = (retained.verified, retained.fuel)
        self.selected = self.context.incumbent_selected
        self.selection_method = retained.selection_method

    def _refine(self) -> None:
        """Improve purchase cost through a verified road-graph proposal."""
        if (
            self.context.national_refiner is not None
            and (not self.recovered)
            and (self.fuel.exact_purchase_cost > 0)
            and (self.budget.attempts <= self.budget.max_attempts - 1)
            and (self.budget.remaining_seconds > 5)
        ):
            distance_limit = maximum_distance(
                self.baseline,
                self.request.max_detour_miles,
                self.request.max_detour_ratio,
            )
            proposal = self.context.national_refiner.propose(
                self.request.start,
                self.request.finish,
                self.request.initial,
                self.request.reserve,
                self.fuel.exact_purchase_cost,
                self.max_total_seconds,
                distance_limit,
                self.budget,
                baseline=self.baseline,
                seed_ids=tuple(v.station.station_id for v in self.selected),
                incumbent_route=self.verified,
            )
            if proposal and tuple(
                item.station.station_id for item in proposal
            ) != tuple(item.station.station_id for item in self.selected):
                try:
                    alternative, alternative_fuel, alternative_selected = (
                        self.context.verify(
                            self.request, self.baseline, proposal, self.budget
                        )
                    )
                except (
                    NoFeasiblePlan,
                    RouteCheckFailed,
                    DetourLimitExceeded,
                    DurationLimitExceeded,
                ) as error:
                    logger.info(
                        "national_proposal_rejected reason=%s", type(error).__name__
                    )
                else:
                    if rank_selection(
                        alternative, alternative_fuel, alternative_selected
                    ) < rank_selection(self.verified, self.fuel, self.selected):
                        self.verified = alternative
                        self.fuel = alternative_fuel
                        self.selected = alternative_selected
                        self.selection_method = (
                            "partial_csv_road_refinement"
                            if self.context.national_refiner.allow_partial
                            else "nationwide_csv_road_refinement"
                        )
