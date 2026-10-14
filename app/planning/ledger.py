"""FIFO inventory valuation for a verified, fixed fuel plan."""

from collections import deque
from dataclasses import dataclass
from decimal import Decimal

from app.domain.fuel import FuelPlan, FuelPurchase, VehicleSpec
from app.domain.ledger import CostLedger, usd_cents
from app.domain.units import finite_nonnegative
from app.planning.fuel import ROUNDING_EPSILON_GALLONS


class InitialFuelPriceRequired(ValueError):
    pass


@dataclass(slots=True)
class _FuelLot:
    gallons: Decimal
    unit_price: Decimal | None
    is_initial: bool


class InventoryLedger:
    """Mutable only within one calculation; consumed lots leave in FIFO order."""

    def __init__(self, initial_gallons: Decimal, unit_price: Decimal | None) -> None:
        finite_nonnegative(initial_gallons, "initial_gallons")
        if unit_price is not None and (not unit_price.is_finite() or unit_price <= 0):
            raise ValueError("initial unit price must be finite and positive")
        self._lots = deque(
            [_FuelLot(initial_gallons, unit_price, True)] if initial_gallons else []
        )
        self.quantity = initial_gallons
        self.consumed_value = Decimal(0)
        self.initial_consumed_value = Decimal(0)

    def consume(self, gallons: Decimal) -> None:
        finite_nonnegative(gallons, "consumed gallons")
        if gallons > self.quantity:
            if gallons - self.quantity > ROUNDING_EPSILON_GALLONS:
                raise ValueError("inventory has insufficient fuel")
            # Division of road meters by fuel economy can leave a sub-epsilon
            # deficit after the optimizer has accepted an effectively empty
            # tank. Consume the actual remaining inventory in that case.
            gallons = self.quantity
        remaining = gallons
        while remaining:
            if not self._lots:
                if remaining > ROUNDING_EPSILON_GALLONS:
                    raise ValueError("inventory has insufficient fuel")
                break
            lot = self._lots[0]
            taken = min(lot.gallons, remaining)
            if lot.unit_price is None:
                raise InitialFuelPriceRequired(
                    "initial fuel was consumed without a valuation price"
                )
            value = taken * lot.unit_price
            self.consumed_value += value
            if lot.is_initial:
                self.initial_consumed_value += value
            lot.gallons -= taken
            remaining -= taken
            if lot.gallons == 0:
                self._lots.popleft()
        # Recompute from actual lots. Repeated Decimal divisions can leave a
        # sub-epsilon difference between a separate quantity accumulator and
        # the sum of lots after several refills.
        self.quantity = sum((lot.gallons for lot in self._lots), Decimal(0))

    def purchase(self, gallons: Decimal, unit_price: Decimal) -> None:
        finite_nonnegative(gallons, "purchased gallons")
        if not unit_price.is_finite() or unit_price <= 0:
            raise ValueError("purchase price must be finite and positive")
        if gallons:
            self._lots.append(_FuelLot(gallons, unit_price, False))
            self.quantity += gallons

    @property
    def ending_value(self) -> Decimal | None:
        if any(lot.gallons and lot.unit_price is None for lot in self._lots):
            return None
        return sum(
            (lot.gallons * lot.unit_price for lot in self._lots if lot.unit_price),
            Decimal(0),
        )


def value_fuel_plan(
    plan: FuelPlan,
    leg_distances_meters: tuple[Decimal, ...],
    initial_fuel_gallons: Decimal,
    initial_unit_price: Decimal | None,
    vehicle: VehicleSpec | None = None,
) -> CostLedger:
    """Value the exact same ordered legs and visits used by the optimizer."""
    vehicle = vehicle or VehicleSpec()
    if len(leg_distances_meters) != len(plan.visits) + 1:
        raise ValueError("ledger legs must match fuel visits")
    if initial_fuel_gallons > vehicle.capacity_gallons:
        raise ValueError("initial fuel exceeds vehicle capacity")
    inventory = InventoryLedger(initial_fuel_gallons, initial_unit_price)
    for index, leg in enumerate(leg_distances_meters):
        inventory.consume(vehicle.gallons_for_meters(leg))
        if index < len(plan.visits):
            visit: FuelPurchase = plan.visits[index]
            if (
                abs(inventory.quantity - visit.arrival_gallons)
                > ROUNDING_EPSILON_GALLONS
            ):
                raise ValueError("fuel plan arrival does not match route consumption")
            inventory.purchase(visit.purchased_gallons, visit.price_per_gallon)
            if (
                abs(inventory.quantity - visit.departure_gallons)
                > ROUNDING_EPSILON_GALLONS
            ):
                raise ValueError("fuel plan departure does not match purchase")
            if inventory.quantity - vehicle.capacity_gallons > ROUNDING_EPSILON_GALLONS:
                raise ValueError("fuel plan exceeds tank capacity")
    if abs(inventory.quantity - plan.ending_gallons) > ROUNDING_EPSILON_GALLONS:
        raise ValueError("fuel plan ending inventory does not reconcile")
    consumed = sum(
        (vehicle.gallons_for_meters(leg) for leg in leg_distances_meters),
        Decimal(0),
    )
    if consumed != plan.consumed_gallons:
        raise ValueError("fuel plan consumption does not reconcile")
    purchase_total = plan.exact_purchase_cost
    displayed_stop_total = sum(
        (usd_cents(visit.exact_cost) for visit in plan.visits), Decimal(0)
    )
    ending_value = inventory.ending_value
    if initial_unit_price is not None and ending_value is not None:
        input_value = initial_fuel_gallons * initial_unit_price + purchase_total
        rounding_value = ROUNDING_EPSILON_GALLONS * max(
            (initial_unit_price, *(visit.price_per_gallon for visit in plan.visits))
        )
        if abs(input_value - inventory.consumed_value - ending_value) > rounding_value:
            raise ValueError("fuel value conservation failed")
    return CostLedger(
        purchased_gallons=plan.purchased_gallons,
        consumed_gallons=consumed,
        ending_gallons=inventory.quantity,
        exact_purchase_cost=purchase_total,
        exact_consumed_cost=inventory.consumed_value,
        exact_initial_value_consumed=inventory.initial_consumed_value,
        exact_ending_value=ending_value,
        purchase_rounding_adjustment=usd_cents(purchase_total) - displayed_stop_total,
    )
