from decimal import Decimal

import pytest

from app.domain.fuel import FuelOpportunity
from app.domain.units import METERS_PER_MILE
from app.planning.fuel import FuelShortfall, optimize_fuel


def miles(*distances: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(value) * METERS_PER_MILE for value in distances)


def stations(*prices: str) -> tuple[FuelOpportunity, ...]:
    return tuple(
        FuelOpportunity(f"station-{index}", Decimal(price))
        for index, price in enumerate(prices)
    )


def test_short_trip_uses_starting_fuel_without_purchase() -> None:
    plan = optimize_fuel((), miles("100"), Decimal("50"))
    assert plan.visits == ()
    assert plan.purchased_gallons == 0
    assert plan.ending_gallons == 40


def test_cheaper_station_receives_only_needed_fuel() -> None:
    plan = optimize_fuel(stations("4", "3"), miles("400", "100", "500"), Decimal("50"))
    assert [visit.purchased_gallons for visit in plan.visits] == [
        Decimal("0"),
        Decimal("50"),
    ]
    assert plan.exact_purchase_cost == Decimal("150")
    assert plan.ending_gallons == 0


def test_equal_price_tie_prefers_earlier_purchase() -> None:
    plan = optimize_fuel(stations("3", "3"), miles("400", "100", "100"), Decimal("50"))
    assert [visit.purchased_gallons for visit in plan.visits] == [
        Decimal("10"),
        Decimal("0"),
    ]


def test_initial_prefix_is_not_refilled_at_origin() -> None:
    with pytest.raises(FuelShortfall) as error:
        optimize_fuel(stations("2"), miles("101", "1"), Decimal("10"))
    assert error.value.leg_index == 0
    assert error.value.missing_gallons == Decimal("0.1")


def test_exact_range_boundary_and_reserve() -> None:
    at_limit = optimize_fuel((), miles("500"), Decimal("50"))
    assert at_limit.ending_gallons == 0
    assert at_limit.consumed_gallons == 50
    with pytest.raises(FuelShortfall):
        optimize_fuel((), miles("501"), Decimal("50"))
    with pytest.raises(FuelShortfall):
        optimize_fuel((), miles("490"), Decimal("50"), Decimal("2"))


def test_zero_leg_station_is_reachable_without_initial_fuel() -> None:
    plan = optimize_fuel(stations("3"), miles("0", "10"), Decimal(0))
    assert plan.visits[0].purchased_gallons == 1


def test_invalid_price_and_distance_rejected() -> None:
    with pytest.raises(ValueError):
        stations("NaN")
    with pytest.raises(ValueError):
        optimize_fuel((), miles("-1"), Decimal("50"))


def test_lowering_one_station_price_cannot_raise_minimum_spend() -> None:
    legs = miles("400", "200", "400")
    original = optimize_fuel(stations("4", "3"), legs, Decimal("50"))
    cheaper = optimize_fuel(stations("2", "3"), legs, Decimal("50"))
    assert cheaper.exact_purchase_cost <= original.exact_purchase_cost
    assert (
        Decimal("50") + cheaper.purchased_gallons - cheaper.consumed_gallons
        == cheaper.ending_gallons
    )


def test_projected_route_roundoff_does_not_create_fuel_shortfall() -> None:
    legs = tuple(
        Decimal(value)
        for value in (
            "9056.5895296884178367505",
            "70320.5498602377999467955",
            "154927.530039144692718414",
            "20.23057092908949804",
        )
    )
    plan = optimize_fuel(
        stations("3.414", "3.40233333", "3.46233333"),
        legs,
        Decimal("3"),
    )
    assert plan.ending_gallons == 0
    assert plan.visits[0].purchased_gallons > 0
