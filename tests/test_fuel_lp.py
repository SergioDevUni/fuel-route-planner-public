"""Independent continuous LP oracle for fixed-sequence purchase cost_search."""

import random
from decimal import Decimal

import numpy as np
import pytest
from scipy.optimize import linprog

from app.domain.fuel import FuelOpportunity, VehicleSpec
from app.domain.units import METERS_PER_MILE
from app.planning.fuel import FuelShortfall, optimize_fuel


@pytest.mark.parametrize("seed", range(100))
def test_fixed_sequence_matches_linear_program(seed: int) -> None:
    randomizer = random.Random(10_000 + seed)
    count = randomizer.randrange(1, 6)
    capacity = 5
    reserve = randomizer.randrange(0, 3)
    initial = randomizer.randrange(reserve * 2, capacity * 2 + 1) / 2
    prices = [randomizer.randrange(100, 701) / 100 for _ in range(count)]
    consumption = [randomizer.randrange(0, 13) / 2 for _ in range(count + 1)]

    rows: list[list[float]] = []
    limits: list[float] = []
    cumulative = 0.0
    for station_index in range(count):
        cumulative += consumption[station_index]
        rows.append([-1.0 if index < station_index else 0.0 for index in range(count)])
        limits.append(initial - cumulative - reserve)
        rows.append([1.0 if index <= station_index else 0.0 for index in range(count)])
        limits.append(capacity - initial + cumulative)
    cumulative += consumption[-1]
    rows.append([-1.0] * count)
    limits.append(initial - cumulative - reserve)
    oracle = linprog(
        c=np.array(prices),
        A_ub=np.array(rows),
        b_ub=np.array(limits),
        bounds=(0, None),
        method="highs",
    )

    opportunities = tuple(
        FuelOpportunity(f"station-{index}", Decimal(str(price)))
        for index, price in enumerate(prices)
    )
    legs = tuple(
        Decimal(str(gallons * 10)) * METERS_PER_MILE for gallons in consumption
    )
    vehicle = VehicleSpec(Decimal(capacity), Decimal(10))
    if oracle.status == 2:
        with pytest.raises(FuelShortfall):
            optimize_fuel(
                opportunities,
                legs,
                Decimal(str(initial)),
                Decimal(reserve),
                vehicle,
            )
    else:
        assert oracle.success, oracle.message
        plan = optimize_fuel(
            opportunities,
            legs,
            Decimal(str(initial)),
            Decimal(reserve),
            vehicle,
        )
        assert abs(float(plan.exact_purchase_cost) - oracle.fun) < 1e-7
