from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.fuel import FuelOpportunity
from app.domain.units import METERS_PER_MILE
from app.planning.fuel import optimize_fuel
from app.planning.ledger import InitialFuelPriceRequired, value_fuel_plan


def miles(*values: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(value) * METERS_PER_MILE for value in values)


def test_short_trip_values_only_consumed_starting_fuel() -> None:
    legs = miles("300")
    plan = optimize_fuel((), legs, Decimal("50"))
    ledger = value_fuel_plan(plan, legs, Decimal("50"), Decimal("3"))
    assert ledger.fuel_purchases_cost == Decimal("0.00")
    assert ledger.total_fuel_cost == Decimal("90.00")
    assert ledger.initial_fuel_value_consumed == Decimal("90.00")
    assert ledger.ending_fuel_value == Decimal("60.00")
    assert ledger.ending_gallons == 20


def test_fifo_ledger_reconciles_partial_refills() -> None:
    legs = miles("400", "200", "400")
    stops = (
        FuelOpportunity("A", Decimal("4")),
        FuelOpportunity("B", Decimal("3")),
    )
    plan = optimize_fuel(stops, legs, Decimal("50"))
    ledger = value_fuel_plan(plan, legs, Decimal("50"), Decimal("3.20"))
    assert [visit.purchased_gallons for visit in plan.visits] == [10, 40]
    assert ledger.purchased_gallons == 50
    assert ledger.consumed_gallons == 100
    assert ledger.fuel_purchases_cost == Decimal("160.00")
    assert ledger.total_fuel_cost == Decimal("320.00")
    assert ledger.ending_fuel_value == 0


def test_price_is_required_only_if_unvalued_initial_fuel_is_consumed() -> None:
    zero = miles("0")
    plan = optimize_fuel((), zero, Decimal("50"))
    ledger = value_fuel_plan(plan, zero, Decimal("50"), None)
    assert ledger.total_fuel_cost == 0
    assert ledger.ending_fuel_value is None
    legs = miles("1")
    plan = optimize_fuel((), legs, Decimal("50"))
    with pytest.raises(InitialFuelPriceRequired):
        value_fuel_plan(plan, legs, Decimal("50"), None)


def test_eight_decimal_price_is_preserved_until_cents_rounding() -> None:
    legs = miles("0", "1")
    plan = optimize_fuel(
        (FuelOpportunity("A", Decimal("3.00733333")),), legs, Decimal(0)
    )
    ledger = value_fuel_plan(plan, legs, Decimal(0), None)
    assert ledger.exact_purchase_cost == Decimal("0.300733333")
    assert ledger.fuel_purchases_cost == Decimal("0.30")
    assert ledger.total_fuel_cost == Decimal("0.30")


def test_sub_epsilon_empty_tank_rounding_reconciles() -> None:
    legs = tuple(Decimal(value) for value in ("31896.4", "30268.7", "11069.5"))
    stops = (
        FuelOpportunity("70108", Decimal("3.50")),
        FuelOpportunity("72113", Decimal("4.00")),
    )
    plan = optimize_fuel(stops, legs, Decimal("2"))
    ledger = value_fuel_plan(plan, legs, Decimal("2"), Decimal("3.20"))
    assert ledger.ending_gallons == 0
    assert plan.visits[1].purchased_gallons == 0


def test_fractional_full_tank_refill_reconciles() -> None:
    legs = tuple(Decimal(value) for value in ("204544.1", "2425.6", "400000", "400000"))
    stops = tuple(
        FuelOpportunity(name, Decimal(price))
        for name, price in (("A", "3.00"), ("B", "3.20"), ("C", "3.40"))
    )
    plan = optimize_fuel(stops, legs, Decimal(20), Decimal(2))
    ledger = value_fuel_plan(plan, legs, Decimal(20), Decimal("3.20"))
    assert plan.visits[1].departure_gallons == 50
    assert abs(ledger.ending_gallons - plan.ending_gallons) <= Decimal("1e-18")
    assert ledger.fuel_purchases_cost == plan.exact_purchase_cost.quantize(
        Decimal("0.01")
    )


def test_sub_epsilon_capacity_excess_does_not_reject_a_valid_full_tank() -> None:
    legs = miles("0", "500")
    plan = optimize_fuel((FuelOpportunity("A", Decimal(3)),), legs, Decimal(20))
    visit = plan.visits[0]
    # Reproduce the 1E-26 reconstruction difference measured on the live trip.
    plan = replace(
        plan,
        visits=(
            replace(visit, purchased_gallons=Decimal("30.00000000000000000000000001")),
        ),
    )
    ledger = value_fuel_plan(plan, legs, Decimal(20), Decimal("3.20"))
    assert ledger.ending_gallons <= Decimal("1e-18")
    assert ledger.fuel_purchases_cost == Decimal("90.00")


def test_real_tank_overfill_still_rejected() -> None:
    legs = miles("10", "490")
    plan = optimize_fuel((FuelOpportunity("A", Decimal(3)),), legs, Decimal(20))
    visit = plan.visits[0]
    overfill = Decimal("50.001") - visit.departure_gallons
    invalid = replace(
        plan,
        visits=(
            replace(
                visit,
                purchased_gallons=visit.purchased_gallons + overfill,
                departure_gallons=Decimal("50.001"),
            ),
        ),
    )
    with pytest.raises(ValueError, match="exceeds tank capacity"):
        value_fuel_plan(invalid, legs, Decimal(20), Decimal("3.20"))
