"""Exact units used by the assessment vehicle."""

from decimal import Decimal

METERS_PER_MILE = Decimal("1609.344")
DEFAULT_MILES_PER_GALLON = Decimal("10")
DEFAULT_CAPACITY_GALLONS = Decimal("50")


def finite_nonnegative(value: Decimal, name: str) -> None:
    if not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
