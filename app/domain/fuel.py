"""Immutable fuel quantities and vehicle invariants."""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.units import (
    DEFAULT_CAPACITY_GALLONS,
    DEFAULT_MILES_PER_GALLON,
    METERS_PER_MILE,
    finite_nonnegative,
)


@dataclass(frozen=True, slots=True)
class VehicleSpec:
    capacity_gallons: Decimal = DEFAULT_CAPACITY_GALLONS
    miles_per_gallon: Decimal = DEFAULT_MILES_PER_GALLON

    def __post_init__(self) -> None:
        if not self.capacity_gallons.is_finite() or self.capacity_gallons <= 0:
            raise ValueError("capacity_gallons must be finite and positive")
        if not self.miles_per_gallon.is_finite() or self.miles_per_gallon <= 0:
            raise ValueError("miles_per_gallon must be finite and positive")

    def gallons_for_meters(self, distance_meters: Decimal) -> Decimal:
        finite_nonnegative(distance_meters, "distance_meters")
        return distance_meters / (METERS_PER_MILE * self.miles_per_gallon)


@dataclass(frozen=True, slots=True)
class FuelOpportunity:
    station_id: str
    price_per_gallon: Decimal

    def __post_init__(self) -> None:
        if not self.station_id:
            raise ValueError("station_id is required")
        if not self.price_per_gallon.is_finite() or self.price_per_gallon <= 0:
            raise ValueError("price_per_gallon must be finite and positive")


@dataclass(frozen=True, slots=True)
class FuelPurchase:
    station_id: str
    arrival_gallons: Decimal
    purchased_gallons: Decimal
    departure_gallons: Decimal
    price_per_gallon: Decimal

    @property
    def exact_cost(self) -> Decimal:
        return self.purchased_gallons * self.price_per_gallon


@dataclass(frozen=True, slots=True)
class FuelPlan:
    visits: tuple[FuelPurchase, ...]
    ending_gallons: Decimal
    consumed_gallons: Decimal

    @property
    def purchased_gallons(self) -> Decimal:
        return sum((visit.purchased_gallons for visit in self.visits), Decimal(0))

    @property
    def exact_purchase_cost(self) -> Decimal:
        return sum((visit.exact_cost for visit in self.visits), Decimal(0))
