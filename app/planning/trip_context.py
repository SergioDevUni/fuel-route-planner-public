"""Request-owned routing dependencies, discovery and verified checkpoints."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from app.domain.fuel import FuelPlan
from app.domain.routing import Coordinates, Route
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget
from app.planning.candidates import (
    CandidateIndex,
    ProjectedCandidate,
    discovery_context,
)
from app.planning.national_refiner import NationalRoadRefiner
from app.planning.ports import RoadMatrixPort, RoutingPort
from app.planning.ranking import rank_plan
from app.planning.request import PlanningRequest
from app.planning.trip_results import finalize_trip
from app.planning.trip_types import PlannedTrip, RouteCheckFailed
from app.routing.client import RoutingTransportUnavailable


class PlanningContext:
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
        self.router = router
        self.candidates = candidates
        self.matrix_provider = matrix_provider
        self.national_refiner = national_refiner
        self.on_verified = on_verified
        self.verification_profile = verification_profile
        self.baseline_retry = baseline_retry
        self.incumbent: PlannedTrip | None = None
        self.incumbent_selected: tuple[ProjectedCandidate, ...] = ()
        self.rejected: set[str] = set()
        self.discovery_budget: RequestBudget | None = None

    def along_route(
        self, route: Route, radius: float = 8000
    ) -> tuple[ProjectedCandidate, ...]:
        if self.discovery_budget is None:
            return self.candidates.along_route(route, radius)
        with discovery_context(self.discovery_budget, self.verification_profile):
            before = CandidateIndex.along_route.cache_info().hits
            result = self.candidates.along_route(route, radius)
            self.discovery_budget.increment_metric(
                f"{self.verification_profile}_projection_requests", 1
            )
            self.discovery_budget.increment_metric(
                f"{self.verification_profile}_projection_cache_hits_observed",
                CandidateIndex.along_route.cache_info().hits - before,
            )
            return result

    def baseline(
        self, start: Coordinates, finish: Coordinates, budget: RequestBudget
    ) -> Route:
        try:
            with budget.stage("baseline_acquisition"):
                return self.router.route((start, finish), budget)
        except RoutingTransportUnavailable:
            if (
                not self.baseline_retry
                or budget.attempts > 1
                or budget.remaining_seconds <= 6
            ):
                raise
            # One bounded transport retry leaves two hosted calls for matrix
            # and selected directions. Never retry rejected keys or 429 status.
            recovery = budget.child(min(3.0, budget.remaining_seconds - 3.5))
            try:
                with budget.stage("baseline_recovery"):
                    return self.router.route((start, finish), recovery)
            except PlanningDeadlineExceeded as error:
                budget.check_deadline()
                raise RoutingTransportUnavailable(
                    "baseline recovery expired"
                ) from error

    def checkpoint(
        self,
        baseline: Route,
        verified: Route,
        fuel: FuelPlan,
        selected: tuple[ProjectedCandidate, ...],
        initial: Decimal,
        price: Decimal | None,
        basis: str,
        budget: RequestBudget,
        method: str,
    ) -> PlannedTrip:
        result = finalize_trip(
            baseline,
            verified,
            selected,
            fuel,
            initial,
            price,
            basis,
            self.candidates.version,
            budget,
            method,
            False,
        )

        if self.incumbent is not None and rank_plan(self.incumbent) < rank_plan(result):
            return self.incumbent
        if any(
            v.snap_distance_meters > 1000
            for v in (verified.waypoints[0], verified.waypoints[-1])
        ):
            raise RouteCheckFailed("endpoint road snap exceeds policy")
        if self.on_verified is not None:
            self.on_verified(result)
        self.incumbent = result
        self.incumbent_selected = selected
        return result

    def verify(
        self,
        request: PlanningRequest,
        baseline: Route,
        selected: tuple[ProjectedCandidate, ...],
        budget: RequestBudget,
        *,
        allow_repair: bool = True,
        route_query: Callable[[tuple[Coordinates, ...], RequestBudget], Route]
        | None = None,
    ) -> tuple[Route, FuelPlan, tuple[ProjectedCandidate, ...]]:
        from app.planning.route_verification import verify_selection

        return verify_selection(
            self,
            baseline,
            selected,
            request.initial,
            request.reserve,
            request.max_detour_miles,
            request.max_detour_ratio,
            budget,
            baseline.duration_seconds + request.max_extra_duration_minutes * 60,
            allow_repair=allow_repair,
            route_query=route_query,
        )

    def complete(
        self,
        request: PlanningRequest,
        baseline: Route,
        verified: Route,
        selected: tuple[ProjectedCandidate, ...],
        fuel: FuelPlan,
        price: Decimal | None,
        basis: str,
        budget: RequestBudget,
        method: str,
        search_complete: bool | None = None,
    ) -> PlannedTrip:
        return finalize_trip(
            baseline,
            verified,
            selected,
            fuel,
            request.initial,
            price,
            basis,
            self.candidates.version,
            budget,
            method,
            search_complete,
        )
