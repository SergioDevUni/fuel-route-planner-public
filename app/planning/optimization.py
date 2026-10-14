"""Product policy for time spent improving a feasible trip."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class OptimizationPolicy:
    mode: str
    use_full_window: bool = False

    @property
    def access_price_penalty(self) -> Decimal:
        return Decimal(0) if self.use_full_window else Decimal("0.02")


BALANCED = OptimizationPolicy("balanced")
FUEL_FOCUSED = OptimizationPolicy("lowest_fuel_cost", use_full_window=True)


def optimization_policy(mode: str) -> OptimizationPolicy:
    if mode == BALANCED.mode:
        return BALANCED
    if mode == FUEL_FOCUSED.mode:
        return FUEL_FOCUSED
    raise ValueError("unsupported optimization mode")
