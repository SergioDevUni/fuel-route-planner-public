"""Safe CSV-offer screening before directed road queries.

Cost bounds screen offers before the road engine supplies directed legs for the
remaining offers. Every offer is included in the screening counts.
"""

from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal

from app.domain.fuel import VehicleSpec
from app.domain.routing import Coordinates
from app.domain.units import finite_nonnegative
from app.planning.candidates import StationCandidate
from app.routing.geometry import geodesic_meters


@dataclass(frozen=True, slots=True)
class ScopedOffer:
    station: StationCandidate
    minimum_purchase_cost: Decimal


@dataclass(frozen=True, slots=True)
class NationalScope:
    possible_offers: tuple[ScopedOffer, ...]
    excluded_by_cost: int
    total_csv_offers: int


@dataclass(frozen=True, slots=True)
class _PriceAccessBound:
    lowest_price: Decimal
    negative_levels: tuple[Decimal, ...]
    prefix_deltas: tuple[Decimal, ...]
    prefix_weighted_levels: tuple[Decimal, ...]

    def cost(self, required_gallons: Decimal) -> Decimal:
        boundary = bisect_right(self.negative_levels, -required_gallons)
        extra = (
            required_gallons * self.prefix_deltas[boundary]
            + self.prefix_weighted_levels[-1]
            - self.prefix_weighted_levels[boundary]
        )
        return required_gallons * self.lowest_price + extra


def _price_access_bound(
    stations: tuple[StationCandidate, ...],
    start_distances: tuple[Decimal, ...],
    initial_gallons: Decimal,
    vehicle: VehicleSpec,
) -> _PriceAccessBound:
    """Lower bound purchased volume above each price before cheaper access.

    To buy at or below a price, the route must first reach a CSV site at or
    below it. Geodesic start distance is a lower bound on fuel used first.
    """
    nearest_by_price: dict[Decimal, Decimal] = {}
    for station, distance in zip(stations, start_distances, strict=True):
        old = nearest_by_price.get(station.price_per_gallon)
        if old is None or distance < old:
            nearest_by_price[station.price_per_gallon] = distance
    prices = tuple(sorted(nearest_by_price))
    nearest = Decimal("Infinity")
    levels: list[Decimal] = []
    for price in prices[:-1]:
        nearest = min(nearest, nearest_by_price[price])
        levels.append(
            max(
                Decimal(0),
                vehicle.gallons_for_meters(nearest) - initial_gallons,
            )
        )
    deltas = tuple(
        later - earlier for earlier, later in zip(prices, prices[1:], strict=False)
    )
    prefix_deltas = [Decimal(0)]
    prefix_weighted = [Decimal(0)]
    for delta, level in zip(deltas, levels, strict=True):
        prefix_deltas.append(prefix_deltas[-1] + delta)
        prefix_weighted.append(prefix_weighted[-1] + delta * level)
    return _PriceAccessBound(
        prices[0],
        tuple(-level for level in levels),
        tuple(prefix_deltas),
        tuple(prefix_weighted),
    )


def screen_csv_offers(
    stations: tuple[StationCandidate, ...],
    start: Coordinates,
    finish: Coordinates,
    initial_gallons: Decimal,
    reserve_gallons: Decimal,
    incumbent_cost: Decimal,
    *,
    vehicle: VehicleSpec | None = None,
    price_access_bound: bool = False,
    account_for_snaps: bool = True,
) -> NationalScope:
    """Keep every CSV offer whose geodesic cost bound can tie or beat a plan.

    Geodesic legs are relaxed by the accepted endpoint/station snap radii.
    Starting fuel is free inventory; all additional gallons
    cost at least the lowest CSV gallon price. Equality is retained because
    another plan could win the deterministic distance tie-break.
    """
    vehicle = vehicle or VehicleSpec()
    finite_nonnegative(initial_gallons, "initial_gallons")
    finite_nonnegative(reserve_gallons, "reserve_gallons")
    finite_nonnegative(incumbent_cost, "incumbent_cost")
    if not reserve_gallons <= initial_gallons <= vehicle.capacity_gallons:
        raise ValueError("initial fuel must fit reserve and tank")
    if not stations:
        return NationalScope((), 0, 0)
    if len({station.station_id for station in stations}) != len(stations):
        raise ValueError("CSV offer IDs must be distinct")
    lowest_price = min(station.price_per_gallon for station in stations)

    def snap_allowance(station: StationCandidate) -> Decimal:
        return (
            (
                Decimal(1000)
                + (
                    Decimal(1000)
                    if station.location_precision == "city_representative"
                    else Decimal(250)
                )
            )
            if account_for_snaps
            else Decimal(0)
        )

    start_distances = tuple(
        max(
            Decimal(0),
            geodesic_meters(start, station.coordinates) - snap_allowance(station),
        )
        for station in stations
    )
    access = (
        _price_access_bound(stations, start_distances, initial_gallons, vehicle)
        if price_access_bound
        else None
    )
    possible: list[ScopedOffer] = []
    excluded = 0
    for station, first_distance in zip(stations, start_distances, strict=True):
        through = first_distance + max(
            Decimal(0),
            geodesic_meters(station.coordinates, finish) - snap_allowance(station),
        )
        required = max(
            Decimal(0),
            vehicle.gallons_for_meters(through) + reserve_gallons - initial_gallons,
        )
        lower_bound = (
            access.cost(required) if access is not None else required * lowest_price
        )
        if lower_bound > incumbent_cost:
            excluded += 1
        else:
            possible.append(ScopedOffer(station, lower_bound))
    possible.sort(
        key=lambda item: (item.minimum_purchase_cost, item.station.station_id)
    )
    return NationalScope(tuple(possible), excluded, len(stations))
