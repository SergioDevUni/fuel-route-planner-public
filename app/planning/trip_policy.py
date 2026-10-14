"""Shared vehicle and driving allowance policy."""

from __future__ import annotations

from decimal import Decimal

from app.domain.routing import Route
from app.domain.units import METERS_PER_MILE
from app.planning.optimization import optimization_policy


def validate_policy(mode: str, extra_minutes: Decimal) -> None:
    optimization_policy(mode)
    if not Decimal(0) <= extra_minutes <= Decimal(30):
        raise ValueError("extra duration limit is outside its accepted range")


def extra_distance(
    baseline: Route, miles: Decimal | None, ratio: Decimal | None
) -> Decimal | None:
    limits = [
        value
        for value in (
            miles * METERS_PER_MILE if miles is not None else None,
            baseline.distance_meters * ratio if ratio is not None else None,
        )
        if value is not None
    ]
    return min(limits) if limits else None


def maximum_distance(
    baseline: Route, miles: Decimal | None, ratio: Decimal | None
) -> Decimal | None:
    extra = extra_distance(baseline, miles, ratio)
    return baseline.distance_meters + extra if extra is not None else None
