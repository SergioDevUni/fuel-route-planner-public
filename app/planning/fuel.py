"""Exact O(n) purchase optimizer for an already ordered fixed route.

Input legs are provider road meters between origin, each listed opportunity, and
destination. The optimizer calculates minimum purchase quantities for the supplied
station sequence. Road access and station selection have separate owners.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.fuel import FuelOpportunity, FuelPlan, FuelPurchase, VehicleSpec
from app.domain.units import finite_nonnegative

ROUNDING_EPSILON_GALLONS = Decimal("1e-18")


@dataclass(slots=True)
class FuelShortfall(ValueError):
    leg_index: int
    missing_gallons: Decimal

    def __str__(self) -> str:
        return f"fuel shortfall on leg {self.leg_index}: {self.missing_gallons} gallons"


def _next_strictly_cheaper(prices: tuple[Decimal, ...]) -> tuple[int, ...]:
    """Return the first later lower-price index; final price is zero sentinel."""
    next_lower = [len(prices) - 1] * (len(prices) - 1)
    stack = [len(prices) - 1]
    for index in range(len(prices) - 2, -1, -1):
        while stack and prices[stack[-1]] >= prices[index]:
            stack.pop()
        next_lower[index] = stack[-1]
        stack.append(index)
    return tuple(next_lower)


def optimize_fuel(
    opportunities: tuple[FuelOpportunity, ...],
    leg_distances_meters: tuple[Decimal, ...],
    initial_fuel_gallons: Decimal,
    reserve_gallons: Decimal = Decimal(0),
    vehicle: VehicleSpec | None = None,
) -> FuelPlan:
    vehicle = vehicle or VehicleSpec()
    if len(leg_distances_meters) != len(opportunities) + 1:
        raise ValueError("one road leg is required between every route event")
    finite_nonnegative(initial_fuel_gallons, "initial_fuel_gallons")
    finite_nonnegative(reserve_gallons, "reserve_gallons")
    if reserve_gallons >= vehicle.capacity_gallons:
        raise ValueError("reserve_gallons must be below capacity")
    if not reserve_gallons <= initial_fuel_gallons <= vehicle.capacity_gallons:
        raise ValueError("initial fuel must fit within reserve and capacity")
    for leg in leg_distances_meters:
        finite_nonnegative(leg, "leg distance")

    consumptions = tuple(
        vehicle.gallons_for_meters(leg) for leg in leg_distances_meters
    )
    progress = [Decimal(0)]
    for consumption in consumptions:
        progress.append(progress[-1] + consumption)
    prices = tuple(item.price_per_gallon for item in opportunities) + (Decimal(0),)
    next_lower = _next_strictly_cheaper(prices)

    visits: list[FuelPurchase] = []
    fuel = initial_fuel_gallons
    for leg_index, consumption in enumerate(consumptions):
        fuel -= consumption
        if fuel < reserve_gallons:
            shortfall = reserve_gallons - fuel
            if shortfall > ROUNDING_EPSILON_GALLONS:
                raise FuelShortfall(leg_index, shortfall)
            fuel = reserve_gallons
        if leg_index == len(opportunities):
            break

        cheaper_index = next_lower[leg_index]
        gallons_to_cheaper = progress[cheaper_index + 1] - progress[leg_index + 1]
        target = (
            gallons_to_cheaper + reserve_gallons
            if gallons_to_cheaper + reserve_gallons <= vehicle.capacity_gallons
            else vehicle.capacity_gallons
        )
        purchase = max(Decimal(0), target - fuel)
        departure = fuel + purchase
        visits.append(
            FuelPurchase(
                station_id=opportunities[leg_index].station_id,
                arrival_gallons=fuel,
                purchased_gallons=purchase,
                departure_gallons=departure,
                price_per_gallon=opportunities[leg_index].price_per_gallon,
            )
        )
        fuel = departure

    return FuelPlan(tuple(visits), fuel, progress[-1])
