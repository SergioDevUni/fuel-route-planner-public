"""Immutable results of FIFO fuel valuation."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def usd_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class CostLedger:
    purchased_gallons: Decimal
    consumed_gallons: Decimal
    ending_gallons: Decimal
    exact_purchase_cost: Decimal
    exact_consumed_cost: Decimal
    exact_initial_value_consumed: Decimal
    exact_ending_value: Decimal | None
    purchase_rounding_adjustment: Decimal

    @property
    def fuel_purchases_cost(self) -> Decimal:
        return usd_cents(self.exact_purchase_cost)

    @property
    def total_fuel_cost(self) -> Decimal:
        return usd_cents(self.exact_consumed_cost)

    @property
    def initial_fuel_value_consumed(self) -> Decimal:
        return usd_cents(self.exact_initial_value_consumed)

    @property
    def ending_fuel_value(self) -> Decimal | None:
        return (
            usd_cents(self.exact_ending_value)
            if self.exact_ending_value is not None
            else None
        )
