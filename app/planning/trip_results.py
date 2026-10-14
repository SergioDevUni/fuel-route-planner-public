"""Fuel reconstruction, starting-fuel valuation and completed results."""

from __future__ import annotations

from decimal import Decimal

from app.domain.fuel import FuelOpportunity, FuelPlan
from app.domain.routing import Coordinates, Route
from app.domain.units import METERS_PER_MILE
from app.planning.budget import RequestBudget
from app.planning.candidates import ProjectedCandidate, StationCandidate
from app.planning.fuel import optimize_fuel
from app.planning.ledger import InitialFuelPriceRequired, value_fuel_plan
from app.planning.trip_types import PlannedTrip
from app.routing.geometry import geodesic_meters


def calculate_fuel(
    stations: tuple[StationCandidate, ...],
    legs: tuple[Decimal, ...],
    initial: Decimal,
    reserve: Decimal,
) -> FuelPlan:
    return optimize_fuel(
        tuple(
            FuelOpportunity(item.station_id, item.price_per_gallon) for item in stations
        ),
        legs,
        initial,
        reserve,
    )


def starting_fuel_price(
    supplied: Decimal | None,
    initial: Decimal,
    start: Coordinates,
    candidates: tuple[StationCandidate, ...],
) -> tuple[Decimal | None, str]:
    if supplied is not None:
        return supplied, "user_supplied"
    if initial == 0:
        return None, "not_needed"
    if not candidates:
        raise InitialFuelPriceRequired("no local starting-fuel price proxy")
    nearest = min(
        candidates,
        key=lambda item: (geodesic_meters(start, item.coordinates), item.station_id),
    )
    if geodesic_meters(start, nearest.coordinates) > 100 * METERS_PER_MILE:
        raise InitialFuelPriceRequired(
            "nearest starting-fuel price proxy is too distant"
        )
    return nearest.price_per_gallon, "nearest_csv_station_proxy"


def finalize_trip(
    baseline: Route,
    verified: Route,
    selected: tuple[ProjectedCandidate, ...],
    fuel: FuelPlan,
    initial: Decimal,
    price: Decimal | None,
    basis: str,
    candidate_version: str,
    budget: RequestBudget,
    method: str,
    search_complete: bool | None = None,
) -> PlannedTrip:
    ledger = value_fuel_plan(
        fuel, tuple(leg.distance_meters for leg in verified.legs), initial, price
    )
    budget.check_deadline()
    return PlannedTrip(
        baseline,
        verified,
        tuple(item.station for item in selected),
        fuel,
        ledger,
        price,
        basis,
        candidate_version,
        budget.attempts,
        method,
        search_complete,
    )
