"""Independent finite-state oracle for small integer-gallon journeys."""

import random
from decimal import Decimal

import pytest

from app.domain.fuel import FuelOpportunity, VehicleSpec
from app.domain.units import METERS_PER_MILE
from app.planning.fuel import FuelShortfall, optimize_fuel


def brute_minimum(
    prices: tuple[int, ...],
    leg_gallons: tuple[int, ...],
    initial: int,
    capacity: int,
    reserve: int,
) -> int | None:
    costs = {initial: 0}
    for index, consumption in enumerate(leg_gallons):
        arrivals = {
            fuel - consumption: cost
            for fuel, cost in costs.items()
            if fuel - consumption >= reserve
        }
        if index == len(prices):
            return min(arrivals.values(), default=None)
        costs = {}
        for arrival, previous_cost in arrivals.items():
            for purchase in range(capacity - arrival + 1):
                departure = arrival + purchase
                cost = previous_cost + purchase * prices[index]
                costs[departure] = min(cost, costs.get(departure, cost))
    raise AssertionError("destination leg missing")


@pytest.mark.parametrize("seed", range(100))
def test_greedy_matches_small_state_oracle(seed: int) -> None:
    randomizer = random.Random(seed)
    capacity = 5
    reserve = randomizer.randrange(0, 3)
    initial = randomizer.randrange(reserve, capacity + 1)
    prices = tuple(
        randomizer.randrange(1, 8) for _ in range(randomizer.randrange(0, 5))
    )
    leg_gallons = tuple(randomizer.randrange(0, 7) for _ in range(len(prices) + 1))
    expected = brute_minimum(prices, leg_gallons, initial, capacity, reserve)

    opportunities = tuple(
        FuelOpportunity(str(index), Decimal(price))
        for index, price in enumerate(prices)
    )
    leg_meters = tuple(
        Decimal(gallons * 10) * METERS_PER_MILE for gallons in leg_gallons
    )
    vehicle = VehicleSpec(Decimal(capacity), Decimal(10))
    if expected is None:
        with pytest.raises(FuelShortfall):
            optimize_fuel(
                opportunities,
                leg_meters,
                Decimal(initial),
                Decimal(reserve),
                vehicle,
            )
    else:
        plan = optimize_fuel(
            opportunities,
            leg_meters,
            Decimal(initial),
            Decimal(reserve),
            vehicle,
        )
        assert plan.exact_purchase_cost == expected
        assert plan.ending_gallons >= reserve
        assert plan.ending_gallons <= capacity
