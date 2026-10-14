"""Continuous fuel choices checked against an independent purchase LP."""

import random
from decimal import Decimal

import pytest
from scipy.optimize import linprog

from app.planning.fuel_profile import FuelCostProfile


def test_crossing_profiles_are_not_dominated() -> None:
    left = FuelCostProfile(((Decimal(0), Decimal(0)), (Decimal(10), Decimal(50))))
    right = FuelCostProfile(((Decimal(0), Decimal(20)), (Decimal(10), Decimal(30))))
    assert not left.covers(right)
    assert not right.covers(left)
    assert left.completion_bound(Decimal(8), Decimal(4)) == Decimal(32)
    assert right.completion_bound(Decimal(8), Decimal(4)) == Decimal(28)


def test_free_extra_inventory_can_dominate_smaller_domain() -> None:
    full = FuelCostProfile.initial(Decimal(10))
    purchased = FuelCostProfile.initial(Decimal(2)).buy(Decimal(3), Decimal(10))
    assert full.covers(purchased)
    assert not purchased.covers(full)
    assert full.drive(Decimal(11)) is None
    with pytest.raises(ValueError, match="domain"):
        full.cost_at(Decimal(9))


def test_profiles_match_lp_at_continuous_arrival_fuel_levels() -> None:
    rng = random.Random(818305)
    for _ in range(100):
        capacity = rng.randint(5, 50)
        initial = Decimal(rng.randint(0, capacity))
        prices = [Decimal(rng.randint(1, 70)) / 10 for _ in range(4)]
        burns = [initial * Decimal("0.7")]
        burns.extend(Decimal(rng.randint(0, capacity * 9)) / 10 for _ in range(4))
        profile = FuelCostProfile.initial(initial).drive(burns[0])
        assert profile is not None
        for price, consumption in zip(prices, burns[1:], strict=True):
            profile = profile.buy(price, Decimal(capacity)).drive(consumption)
            assert profile is not None
        samples = {fuel for fuel, _ in profile.points}
        samples.add((profile.minimum_fuel + profile.maximum_fuel) / 2)
        total_consumed = sum(burns)
        for final_fuel in samples:
            constraints, ceilings = [], []
            for index in range(4):
                constraints.append([-1 if j < index else 0 for j in range(4)])
                ceilings.append(float(initial - sum(burns[: index + 1])))
                constraints.append([1 if j <= index else 0 for j in range(4)])
                ceilings.append(
                    float(Decimal(capacity) - initial + sum(burns[: index + 1]))
                )
            answer = linprog(
                [float(p) for p in prices],
                A_ub=constraints,
                b_ub=ceilings,
                A_eq=[[1] * 4],
                b_eq=[float(total_consumed + final_fuel - initial)],
                bounds=[(0, None)] * 4,
                method="highs",
            )
            assert answer.success
            assert abs(float(profile.cost_at(final_fuel)) - answer.fun) < 1e-7
