"""Public trip-planner facade over request-owned planning stages."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.planning.candidates import CandidateIndex
from app.planning.hosted_planning import plan_hosted
from app.planning.local_planning import plan_local_first
from app.planning.national_refiner import NationalRoadRefiner
from app.planning.ports import RoadMatrixPort, RoutingPort
from app.planning.request import PlanningRequest
from app.planning.route_verification import verify_cross_profile
from app.planning.trip_context import PlanningContext
from app.planning.trip_types import PlannedTrip


class TripPlanner:
    def __init__(
        self,
        router: RoutingPort,
        candidates: CandidateIndex,
        matrix_provider: RoadMatrixPort | None = None,
        national_refiner: NationalRoadRefiner | None = None,
        on_verified: Callable[[PlannedTrip], None] | None = None,
        verification_profile: str = "hosted",
        baseline_retry: bool = True,
    ) -> None:
        self._context = PlanningContext(
            router,
            candidates,
            matrix_provider,
            national_refiner,
            on_verified,
            verification_profile,
            baseline_retry,
        )

    def verified_incumbent(self) -> PlannedTrip | None:
        return self._context.incumbent

    def plan(
        self,
        start: Coordinates,
        finish: Coordinates,
        initial: Decimal,
        reserve: Decimal,
        max_detour_miles: Decimal | None,
        max_detour_ratio: Decimal | None,
        initial_price: Decimal | None = None,
        optimization_mode: str = "balanced",
        max_extra_duration_minutes: Decimal = Decimal(30),
        matrix_first: bool = False,
        budget: RequestBudget | None = None,
    ) -> PlannedTrip:
        return self.plan_request(
            PlanningRequest(
                start,
                finish,
                initial,
                reserve,
                max_detour_miles,
                max_detour_ratio,
                initial_price,
                optimization_mode,
                max_extra_duration_minutes,
            ),
            matrix_first=matrix_first,
            budget=budget,
        )

    def plan_local_first(
        self,
        start: Coordinates,
        finish: Coordinates,
        initial: Decimal,
        reserve: Decimal,
        max_detour_miles: Decimal | None,
        max_detour_ratio: Decimal | None,
        initial_price: Decimal | None = None,
        optimization_mode: str = "balanced",
        max_extra_duration_minutes: Decimal = Decimal(30),
        budget: RequestBudget | None = None,
    ) -> PlannedTrip:
        return self.plan_request(
            PlanningRequest(
                start,
                finish,
                initial,
                reserve,
                max_detour_miles,
                max_detour_ratio,
                initial_price,
                optimization_mode,
                max_extra_duration_minutes,
            ),
            local_first=True,
            budget=budget,
        )

    def plan_request(
        self,
        request: PlanningRequest,
        *,
        local_first: bool = False,
        matrix_first: bool = False,
        budget: RequestBudget | None = None,
    ) -> PlannedTrip:
        if local_first:
            return plan_local_first(self._context, request, budget=budget)
        return plan_hosted(
            self._context, request, matrix_first=matrix_first, budget=budget
        )

    def verify_local_candidate_on_hosted_profile(
        self,
        hosted: PlannedTrip,
        local: PlannedTrip,
        initial: Decimal,
        reserve: Decimal,
        max_detour_miles: Decimal | None,
        max_detour_ratio: Decimal | None,
        max_extra_duration_minutes: Decimal,
        budget: RequestBudget,
    ) -> PlannedTrip:
        return verify_cross_profile(
            self._context,
            hosted=hosted,
            local=local,
            initial=initial,
            reserve=reserve,
            max_detour_miles=max_detour_miles,
            max_detour_ratio=max_detour_ratio,
            max_extra_duration_minutes=max_extra_duration_minutes,
            budget=budget,
        )
