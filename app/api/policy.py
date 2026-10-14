"""Final verified-plan policy at the API success boundary."""

from __future__ import annotations

import logging
from decimal import Decimal

from app.planning.budget import RequestBudget
from app.planning.trip_types import DurationLimitExceeded, PlannedTrip

logger = logging.getLogger("app.api.service")


def check_final_plan_policy(
    result: PlannedTrip, max_extra_minutes: Decimal, budget: RequestBudget
) -> None:
    """Apply the time policy once more at the API's common success boundary."""
    budget.check_deadline()
    if (
        result.verified.duration_seconds
        > result.baseline.duration_seconds + max_extra_minutes * 60
    ):
        raise DurationLimitExceeded("verified route exceeds the travel time limit")
