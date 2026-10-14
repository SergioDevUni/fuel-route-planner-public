"""Map planning outcomes to concise HTTP guidance and server-side diagnostics."""

import logging
from dataclasses import dataclass

from app.api.deadline import PlanningCapacityExceeded
from app.api.errors import api_error
from app.domain.location import LocationAmbiguous, LocationNotFound, LocationOutsideUS
from app.planning.budget import PlanningDeadlineExceeded, RoutingBudgetExceeded
from app.planning.ledger import InitialFuelPriceRequired
from app.planning.trip_types import (
    DetourLimitExceeded,
    DurationLimitExceeded,
    NoFeasiblePlan,
    RouteCheckFailed,
)
from app.routing.client import (
    NoDrivableRoute,
    RoutingAuthenticationFailed,
    RoutingLimitExceeded,
    RoutingRequestRejected,
    RoutingUnavailable,
)
from app.routing.local_valhalla import LocalValhallaUnavailable
from app.routing.normalizer import RoutingResponseInvalid

logger = logging.getLogger("app.api.operations")


@dataclass(frozen=True)
class FailureRule:
    error_type: type[Exception] | tuple[type[Exception], ...]
    code: str
    message: str
    status: int
    event: str | None = None
    level: str = "error"
    include_exception: bool = False


RULES = (
    FailureRule(
        LocationAmbiguous,
        "LOCATION_AMBIGUOUS",
        "Choose a more specific place.",
        422,
        None,
        "error",
        False,
    ),
    FailureRule(
        LocationNotFound,
        "LOCATION_NOT_FOUND",
        "Place was not found.",
        422,
        None,
        "error",
        False,
    ),
    FailureRule(
        LocationOutsideUS,
        "LOCATION_OUTSIDE_US",
        "Endpoints must be in a US state or DC.",
        422,
        None,
        "error",
        False,
    ),
    FailureRule(
        InitialFuelPriceRequired,
        "INITIAL_FUEL_PRICE_REQUIRED",
        "Provide an initial fuel price for this trip.",
        422,
        None,
        "error",
        False,
    ),
    FailureRule(
        NoFeasiblePlan,
        "TRIP_OPTIONS_REQUIRED",
        "Adjust your starting fuel or choose different endpoints.",
        422,
        "trip has no feasible fuel plan",
        "info",
        False,
    ),
    FailureRule(
        DetourLimitExceeded,
        "DETOUR_LIMIT_EXCEEDED",
        "Increase your driving allowance or choose different endpoints.",
        422,
        None,
        "error",
        False,
    ),
    FailureRule(
        DurationLimitExceeded,
        "DURATION_LIMIT_EXCEEDED",
        "Increase your driving allowance or choose different endpoints.",
        422,
        None,
        "error",
        False,
    ),
    FailureRule(
        RouteCheckFailed,
        "ROUTE_CHECK_LIMIT_REACHED",
        "Try planning this trip again or adjust your trip settings.",
        422,
        None,
        "error",
        False,
    ),
    FailureRule(
        NoDrivableRoute,
        "NO_DRIVABLE_ROUTE",
        "Choose endpoints with road access.",
        422,
        None,
        "error",
        False,
    ),
    FailureRule(
        RoutingLimitExceeded,
        "ROUTE_LIMIT_EXCEEDED",
        "Choose closer endpoints for this trip.",
        422,
        None,
        "error",
        False,
    ),
    FailureRule(
        RoutingRequestRejected,
        "NO_DRIVABLE_ROUTE",
        "Choose endpoints with road access.",
        422,
        "api_routing_rejected",
        "error",
        True,
    ),
    FailureRule(
        PlanningCapacityExceeded,
        "PLANNING_BUSY",
        "Trip planning is busy. Try again shortly.",
        503,
        None,
        "error",
        False,
    ),
    FailureRule(
        PlanningDeadlineExceeded,
        "PLANNING_TIMEOUT",
        "Please try planning your trip again.",
        504,
        None,
        "error",
        False,
    ),
    FailureRule(
        RoutingBudgetExceeded,
        "ROUTE_CHECK_LIMIT_REACHED",
        "Try planning this trip again or adjust your trip settings.",
        422,
        None,
        "error",
        False,
    ),
    FailureRule(
        (RoutingUnavailable, RoutingAuthenticationFailed, LocalValhallaUnavailable),
        "ROUTING_UNAVAILABLE",
        "Routing is temporarily unavailable.",
        503,
        "api_routing_unavailable",
        "error",
        True,
    ),
    FailureRule(
        RoutingResponseInvalid,
        "ROUTING_RESPONSE_INVALID",
        "Please try planning your trip again.",
        502,
        "api_routing_response_invalid",
        "error",
        True,
    ),
    FailureRule(
        (FileNotFoundError, OSError, KeyError),
        "DATASET_NOT_READY",
        "Trip planning is temporarily unavailable.",
        503,
        "api_dataset_not_ready",
        "error",
        False,
    ),
    FailureRule(
        ValueError,
        "DATASET_NOT_READY",
        "Trip planning is temporarily unavailable.",
        503,
        "api_dataset_invalid",
        "error",
        False,
    ),
)

TRIP_FAILURES = tuple(
    error_type
    for rule in RULES
    for error_type in (
        rule.error_type if isinstance(rule.error_type, tuple) else (rule.error_type,)
    )
)


def trip_failure(error, request_id):
    for rule in RULES:
        if not isinstance(error, rule.error_type):
            continue
        if rule.event:
            extra = {"request_id": request_id}
            if rule.include_exception:
                extra["exception_type"] = type(error).__name__
            getattr(logger, rule.level)(rule.event, extra=extra)
        response = api_error(rule.code, rule.message, rule.status, request_id)
        if isinstance(error, PlanningCapacityExceeded):
            response["Retry-After"] = "1"
        return response
    raise error
