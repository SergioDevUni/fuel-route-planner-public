"""Execute configured planning strategies and publish verified results."""

from __future__ import annotations

import logging
from concurrent.futures import Future
from dataclasses import dataclass, field, replace
from decimal import Decimal

from app.api.assets import current_candidate_hash, load_candidates
from app.api.policy import check_final_plan_policy
from app.api.runtime import RoutingRuntime, create_runtime
from app.api.serialization import serialize_trip, zero_distance_payload
from app.api.validation import TripInput
from app.domain.fuel import VehicleSpec
from app.domain.location import ResolvedLocation
from app.domain.units import METERS_PER_MILE
from app.planning.baseline_cache import CachedBaselineRouter, local_baseline_route_cache
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget
from app.planning.candidates import CandidateIndex
from app.planning.portfolio import choose_portfolio_plan, needs_local_expansion
from app.planning.ranking import rank_plan
from app.planning.request import PlanningRequest
from app.planning.trip import TripPlanner
from app.planning.trip_types import (
    DetourLimitExceeded,
    DurationLimitExceeded,
    NoFeasiblePlan,
    PlannedTrip,
    RouteCheckFailed,
)
from app.routing.client import RoutingAuthenticationFailed, RoutingUnavailable
from app.routing.geometry import geodesic_meters
from app.routing.local_valhalla import LocalValhalla, LocalValhallaUnavailable
from app.stations.access_review import record_selected_locality_stops

logger = logging.getLogger("app.api.service")


def calculate_uncached(
    trip: TripInput,
    candidate_hash: str | None,
    start: ResolvedLocation,
    finish: ResolvedLocation,
    budget: RequestBudget,
) -> dict:
    budget.check_deadline()
    if start.coordinates == finish.coordinates:
        return zero_distance_payload(trip, start, finish)
    runtime = create_runtime(candidate_hash, budget)
    return ExecutionStages(trip, candidate_hash, start, finish, budget, runtime).run()


@dataclass
class ExecutionStages:
    trip: TripInput
    candidate_hash: str | None
    start: ResolvedLocation
    finish: ResolvedLocation
    budget: RequestBudget
    runtime: RoutingRuntime
    pending_portfolio: tuple[Future, ...] = ()
    candidates: CandidateIndex = field(init=False)
    result: PlannedTrip | None = field(init=False)

    @property
    def planning_request(self) -> PlanningRequest:
        return PlanningRequest(
            self.start.coordinates,
            self.finish.coordinates,
            self.trip.initial_fuel_gallons,
            self.trip.reserve_gallons,
            self.trip.max_detour_miles,
            self.trip.max_detour_ratio,
            self.trip.initial_fuel_price_per_gallon,
            self.trip.optimization_mode,
            self.trip.max_extra_duration_minutes,
        )

    def payload_for(self, result: PlannedTrip) -> dict:
        return serialize_trip(self.trip, self.start, self.finish, result)

    def publish(self, result: PlannedTrip, profile: str = "hosted") -> None:
        check_final_plan_policy(
            result, self.trip.max_extra_duration_minutes, self.budget
        )
        with self.budget.stage("checkpoint_serialization"):
            payload = self.payload_for(result)
        self.budget.publish_verified_payload(
            payload,
            rank_plan(result),
            profile,
        )

    def capture_pending(self, futures: tuple[Future, ...]) -> None:
        self.pending_portfolio = futures

    def run(self) -> dict:
        """Own routing lifetime across strategy selection and final publication."""
        try:
            self.candidates = load_candidates(
                self.runtime.active.source_sha256,
                self.candidate_hash or current_candidate_hash(),
                self.budget,
            )
            self.result = None
            usable_meters = (
                (self.trip.initial_fuel_gallons - self.trip.reserve_gallons)
                * VehicleSpec().miles_per_gallon
                * METERS_PER_MILE
            )
            road_candidate_mode = (
                self.runtime.partial_config is not None
                and self.runtime.national_local is not None
                and (self.runtime.national_refiner is not None)
                and (
                    geodesic_meters(self.start.coordinates, self.finish.coordinates)
                    > usable_meters * Decimal("0.8")
                )
                and (self.budget.remaining_seconds > 11)
            )
            straight_line = geodesic_meters(
                self.start.coordinates, self.finish.coordinates
            )
            full_tank_meters = (
                (VehicleSpec().capacity_gallons - self.trip.reserve_gallons)
                * VehicleSpec().miles_per_gallon
                * METERS_PER_MILE
            )
            if road_candidate_mode and self.budget.settings.partial_portfolio:
                self._portfolio(
                    0.0 if straight_line > usable_meters + full_tank_meters else 3.5
                )
            elif road_candidate_mode and self.budget.settings.partial_local_first:
                self._local_first()
            if self.result is None:
                cached = self._default()
                if cached is not None:
                    return cached
            assert self.result is not None
            check_final_plan_policy(
                self.result, self.trip.max_extra_duration_minutes, self.budget
            )
            self.publish(
                self.result,
                "local"
                if self.result.selection_method.startswith(
                    ("local_road", "csv_road_first")
                )
                else "hosted",
            )
        finally:
            self.runtime.close_when_finished(self.pending_portfolio)
        payload = self.payload_for(self.result)
        protected = self.budget.verified_payload()
        if protected is None or protected == payload:
            record_selected_locality_stops(self.result)
        return protected or payload

    def _portfolio(self, hedge: float) -> None:
        """Run hosted and local alternatives with shared request limits."""
        assert (
            self.runtime.local_baseline_router is not None
            and self.runtime.national_refiner is not None
        )
        hosted_planner = TripPlanner(
            self.runtime.router,
            self.candidates,
            matrix_provider=self.runtime.matrix_provider,
            on_verified=self.publish,
            baseline_retry=False,
        )
        local_planner = TripPlanner(
            self.runtime.local_baseline_router,
            self.candidates,
            national_refiner=self.runtime.national_refiner,
            on_verified=lambda plan: self.publish(plan, "local"),
            verification_profile="local",
        )

        def plan_hosted(phase_budget: RequestBudget) -> PlannedTrip:
            return hosted_planner.plan_request(
                self.planning_request,
                matrix_first=self.runtime.matrix_first,
                budget=phase_budget,
            )

        def plan_local(phase_budget: RequestBudget) -> PlannedTrip:
            return local_planner.plan_request(
                self.planning_request, local_first=True, budget=phase_budget
            )

        self.result = choose_portfolio_plan(
            plan_hosted,
            plan_local,
            lambda hosted,
            local_plan,
            shared_budget: hosted_planner.verify_local_candidate_on_hosted_profile(
                hosted,
                local_plan,
                self.trip.initial_fuel_gallons,
                self.trip.reserve_gallons,
                self.trip.max_detour_miles,
                self.trip.max_detour_ratio,
                self.trip.max_extra_duration_minutes,
                shared_budget,
            ),
            self.budget,
            hedge_delay_seconds=hedge,
            expand_hosted=needs_local_expansion,
            on_pending=self.capture_pending,
            hosted_incumbent=hosted_planner.verified_incumbent,
            local_incumbent=local_planner.verified_incumbent,
        )

    def _local_first(self) -> None:
        """Use local-first discovery while preserving hosted recovery time."""
        assert (
            self.runtime.local_baseline_router is not None
            and self.runtime.national_refiner is not None
        )
        local_budget = self.budget.child(min(10.0, self.budget.remaining_seconds - 4.0))
        try:
            self.result = TripPlanner(
                self.runtime.local_baseline_router,
                self.candidates,
                national_refiner=self.runtime.national_refiner,
                on_verified=lambda plan: self.publish(plan, "local"),
                verification_profile="local",
            ).plan_request(self.planning_request, local_first=True, budget=local_budget)
        except (
            NoFeasiblePlan,
            RouteCheckFailed,
            DetourLimitExceeded,
            DurationLimitExceeded,
            LocalValhallaUnavailable,
            PlanningDeadlineExceeded,
        ) as error:
            logger.info("local_first_fallback reason=%s", type(error).__name__)

    def _default(self) -> dict | None:
        """Execute the primary strategy or retain a verified transport fallback."""
        default_refiner = (
            None
            if self.runtime.partial_config is not None
            and self.budget.settings.partial_portfolio
            else self.runtime.national_refiner
        )
        planner = TripPlanner(
            self.runtime.router,
            self.candidates,
            matrix_provider=self.runtime.matrix_provider,
            national_refiner=default_refiner,
            on_verified=lambda plan: self.publish(
                plan,
                "local"
                if self.runtime.router is self.runtime.local_baseline_router
                else "hosted",
            ),
            baseline_retry=not (
                self.budget.settings.valhalla_url
                and self.budget.settings.valhalla_version
            ),
        )
        request = self.planning_request
        try:
            self.result = planner.plan_request(
                request, matrix_first=self.runtime.matrix_first, budget=self.budget
            )
        except RoutingUnavailable as error:
            if isinstance(error, RoutingAuthenticationFailed):
                raise
            cached = self.budget.verified_payload()
            if cached is not None:
                self.budget.check_deadline()
                self.budget.increment_metric("verified_plan_recoveries", 1)
                return cached
            self.result = self._transport_fallback(request)

        return None

    def _transport_fallback(self, request: PlanningRequest) -> PlannedTrip:
        """Verify a fallback on one coherent local road profile."""
        fallback_url = self.budget.settings.valhalla_url
        fallback_version = self.budget.settings.valhalla_version
        if (
            not fallback_url
            or not fallback_version
            or self.budget.remaining_seconds <= 2
        ):
            raise
        if self.runtime.national_local is None:
            self.runtime.national_local = LocalValhalla(
                fallback_url,
                fallback_version,
                request_timeout_seconds=min(
                    6.0, max(0.1, self.budget.remaining_seconds - 0.5)
                ),
            )
        fallback_router = CachedBaselineRouter(
            self.runtime.national_local,
            local_baseline_route_cache,
            f"valhalla-auto:{fallback_url}:{fallback_version}",
        )
        with self.budget.stage("coherent_local_transport_fallback"):
            self.result = TripPlanner(
                fallback_router,
                self.candidates,
                on_verified=lambda plan: self.publish(
                    replace(plan, selection_method="local_road_recovery"),
                    "local",
                ),
                verification_profile="local",
            ).plan_request(request, budget=self.budget)
        self.result = replace(self.result, selection_method="local_road_recovery")
        return self.result
