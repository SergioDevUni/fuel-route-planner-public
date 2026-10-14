"""Continuous purchase-cost/fuel choices for a fixed path prefix.

Fuel is usable inventory above the reserve. Breakpoints represent a convex,
nondecreasing function, not discretized tank levels. Prices are nonnegative;
extra arrival fuel can therefore replace later purchases without harming a
continuation. All time and visited-node checks belong to the search label.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class FuelCostProfile:
    points: tuple[tuple[Decimal, Decimal], ...]

    @classmethod
    def initial(cls, usable_gallons: Decimal) -> "FuelCostProfile":
        return cls(((usable_gallons, Decimal(0)),))

    @property
    def minimum_fuel(self) -> Decimal:
        return self.points[0][0]

    @property
    def maximum_fuel(self) -> Decimal:
        return self.points[-1][0]

    def cost_at(self, fuel: Decimal) -> Decimal:
        if not self.minimum_fuel <= fuel <= self.maximum_fuel:
            raise ValueError("fuel outside profile domain")
        previous_fuel, previous_cost = self.points[0]
        for next_fuel, next_cost in self.points[1:]:
            if fuel <= next_fuel:
                return previous_cost + (fuel - previous_fuel) * (
                    (next_cost - previous_cost) / (next_fuel - previous_fuel)
                )
            previous_fuel, previous_cost = next_fuel, next_cost
        return previous_cost

    def buy(self, price: Decimal, capacity: Decimal) -> "FuelCostProfile":
        """Minimize f(arrival) + price * (departure - arrival)."""
        kept = [self.points[0]]
        for point in self.points[1:]:
            before_fuel, before_cost = kept[-1]
            slope = (point[1] - before_cost) / (point[0] - before_fuel)
            if slope > price:
                break
            kept.append(point)
        fuel, cost = kept[-1]
        if fuel < capacity:
            kept.append((capacity, cost + (capacity - fuel) * price))
        return FuelCostProfile(tuple(kept))

    def drive(self, consumption: Decimal) -> "FuelCostProfile | None":
        if consumption > self.maximum_fuel:
            return None
        first = max(self.minimum_fuel, consumption)
        points = [(first - consumption, self.cost_at(first))]
        points.extend(
            (fuel - consumption, cost) for fuel, cost in self.points if fuel > first
        )
        return FuelCostProfile(tuple(points))

    def completion_bound(self, consumption: Decimal, price: Decimal) -> Decimal:
        """Relax the suffix to unlimited immediate fuel at an optimistic price."""
        choices = [
            cost + max(Decimal(0), consumption - fuel) * price
            for fuel, cost in self.points
        ]
        if self.minimum_fuel < consumption < self.maximum_fuel:
            choices.append(self.cost_at(consumption))
        return min(choices)

    def covers(self, other: "FuelCostProfile") -> bool:
        """Pointwise no-more-expensive arrival with at least as much inventory.

        Between union breakpoints both compared functions are affine, so their
        difference cannot attain an interior maximum. This is whole-profile
        dominance: crossing profiles must both survive.
        """
        # Nondecreasing costs make the minimum-cost comparison a necessary
        # condition. Reject it before segment interpolation in hot frontiers.
        if (
            self.maximum_fuel < other.maximum_fuel
            or self.points[0][1] > other.points[0][1]
        ):
            return False
        if self.minimum_fuel >= other.maximum_fuel:
            return True
        lo, hi = other.minimum_fuel, other.maximum_fuel
        left = right = 0
        fuel = lo

        def value(
            points: tuple[tuple[Decimal, Decimal], ...], index: int, q: Decimal
        ) -> Decimal:
            x, cost = points[index]
            if index + 1 == len(points):
                return cost
            next_x, next_cost = points[index + 1]
            return cost + (q - x) * ((next_cost - cost) / (next_x - x))

        while True:
            # Advance sorted segments once, instead of rescanning each curve
            # for every union breakpoint. Keep cost_at's arithmetic order.
            supplied = max(fuel, self.minimum_fuel)
            while left + 1 < len(self.points) and self.points[left + 1][0] < supplied:
                left += 1
            while right + 1 < len(other.points) and other.points[right + 1][0] < fuel:
                right += 1

            if value(self.points, left, supplied) > value(other.points, right, fuel):
                return False
            if fuel == hi:
                return True
            # The end of the current segment can equal the just-tested point.
            while left + 1 < len(self.points) and self.points[left + 1][0] <= fuel:
                left += 1
            while right + 1 < len(other.points) and other.points[right + 1][0] <= fuel:
                right += 1
            fuel = min(
                hi,
                self.points[left + 1][0] if left + 1 < len(self.points) else hi,
                other.points[right + 1][0] if right + 1 < len(other.points) else hi,
            )
