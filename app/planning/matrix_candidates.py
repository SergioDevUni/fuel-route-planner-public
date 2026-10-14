"""Bounded, progress-spread CSV offers for a directed road matrix."""

from bisect import bisect_right
from dataclasses import dataclass, field
from decimal import Decimal
from math import ceil

from app.domain.fuel import VehicleSpec
from app.domain.routing import Coordinates
from app.domain.units import METERS_PER_MILE
from app.planning.candidates import ProjectedCandidate
from app.planning.ports import MAX_HOSTED_MATRIX_LOCATIONS, MAX_PUBLIC_MATRIX_LOCATIONS
from app.routing.geometry import geodesic_meters

MAX_MATRIX_OFFERS = 64
Rank = tuple[Decimal, Decimal, str]


@dataclass
class _OfferSelection:
    offers: dict[str, ProjectedCandidate]
    max_locations: int
    max_offers: int
    penalty: Decimal
    points: set[Coordinates] = field(init=False)
    ranks: dict[ProjectedCandidate, Rank] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.points = {v.station.coordinates for v in self.offers.values()}

    def rank(self, item: ProjectedCandidate) -> Rank:
        ranked = self.ranks.get(item)
        if ranked is None:
            ranked = (
                item.station.price_per_gallon
                + self.penalty * item.offset_meters / METERS_PER_MILE,
                item.offset_meters,
                item.station.station_id,
            )
            self.ranks[item] = ranked
        return ranked

    def add(self, item: ProjectedCandidate) -> None:
        if (
            item.station.station_id in self.offers
            or len(self.offers) >= self.max_offers
        ):
            return
        point = item.station.coordinates
        if point not in self.points and len(self.points) >= self.max_locations - 2:
            return
        self.offers[item.station.station_id] = item
        self.points.add(point)

    def first_stops(
        self,
        offers: tuple[ProjectedCandidate, ...],
        start: Coordinates,
        first_range: Decimal,
    ) -> None:
        # Shared physical points need one distance calculation, retaining every offer.
        distances = {
            point: geodesic_meters(start, point)
            for point in dict.fromkeys(v.station.coordinates for v in offers)
        }
        reachable = [
            v for v in offers if distances[v.station.coordinates] <= first_range + 2000
        ]
        if reachable:
            self.add(
                min(
                    reachable,
                    key=lambda v: (distances[v.station.coordinates], self.rank(v)),
                )
            )
            self.add(min(reachable, key=self.rank))

    def bridges(
        self,
        offers: tuple[ProjectedCandidate, ...],
        boundaries: list[Decimal],
        width: Decimal,
    ) -> None:
        for anchor in boundaries[1:-1]:
            bridges = [
                v
                for v in offers
                if max(Decimal(0), anchor - width / 2) <= v.progress_meters <= anchor
            ]
            if bridges:
                self.add(
                    min(
                        bridges,
                        key=lambda v: (
                            anchor - v.progress_meters + v.offset_meters * 2,
                            self.rank(v),
                        ),
                    )
                )

    def fill(self, groups: list[list[ProjectedCandidate]]) -> None:
        for group in groups:
            self.add(min(group, key=self.rank))
        for group in groups:
            self.add(min(group, key=lambda v: (v.offset_meters, self.rank(v))))
        for group in groups:
            self.add(
                min(group, key=lambda v: (v.station.price_per_gallon, self.rank(v)))
            )
        queues = [iter(sorted(group, key=self.rank)) for group in groups]
        while queues and len(self.offers) < self.max_offers:
            active = []
            for queue in queues:
                queued = next(queue, None)
                if queued is not None:
                    self.add(queued)
                    active.append(queue)
            queues = active


def _eligible_offers(
    alternatives: tuple[ProjectedCandidate, ...],
    route_distance: Decimal,
) -> tuple[ProjectedCandidate, ...]:
    eligible: dict[str, ProjectedCandidate] = {}
    for item in alternatives:
        if not 0 <= item.progress_meters <= route_distance:
            continue
        station_id = item.station.station_id
        previous = eligible.get(station_id)
        if previous is None or (item.offset_meters, item.progress_meters) < (
            previous.offset_meters,
            previous.progress_meters,
        ):
            eligible[station_id] = item
    return tuple(eligible.values())


def _progress_groups(
    offers: tuple[ProjectedCandidate, ...],
    boundaries: list[Decimal],
) -> list[list[ProjectedCandidate]]:
    buckets: list[list[ProjectedCandidate]] = [[] for _ in range(len(boundaries) - 1)]
    for item in offers:
        bucket = min(
            len(buckets) - 1, bisect_right(boundaries, item.progress_meters) - 1
        )
        buckets[bucket].append(item)
    return [group for group in buckets if group]


def select_matrix_candidates(
    provisional: tuple[ProjectedCandidate, ...],
    alternatives: tuple[ProjectedCandidate, ...],
    route_distance_meters: Decimal,
    *,
    start: Coordinates | None = None,
    max_locations: int = MAX_HOSTED_MATRIX_LOCATIONS,
    max_offers: int = MAX_MATRIX_OFFERS,
    offset_price_penalty_per_mile: Decimal = Decimal(0),
    initial_gallons: Decimal = Decimal(50),
    reserve_gallons: Decimal = Decimal(0),
) -> tuple[ProjectedCandidate, ...] | None:
    """Preserve fuel bridges, then spread price/access alternatives along progress.

    Geometry ranks discovery; measured directed legs decide feasibility.
    """
    vehicle = VehicleSpec()
    if (
        not 2 <= max_locations <= MAX_PUBLIC_MATRIX_LOCATIONS
        or max_offers < 1
        or offset_price_penalty_per_mile < 0
        or not 0 <= reserve_gallons <= initial_gallons <= vehicle.capacity_gallons
    ):
        raise ValueError("invalid candidate selection limits")
    if route_distance_meters <= 0:
        return None
    selection = _OfferSelection(
        {v.station.station_id: v for v in provisional},
        max_locations,
        max_offers,
        offset_price_penalty_per_mile,
    )
    if len(selection.offers) > max_offers or len(selection.points) > max_locations - 2:
        return None
    offers = _eligible_offers(alternatives, route_distance_meters)
    full_range = (
        (vehicle.capacity_gallons - reserve_gallons)
        * vehicle.miles_per_gallon
        * METERS_PER_MILE
    )
    first_range = (
        (initial_gallons - reserve_gallons) * vehicle.miles_per_gallon * METERS_PER_MILE
    )
    if start is not None:
        selection.first_stops(offers, start, first_range)
    width = min(max(Decimal(1), full_range / 2), route_distance_meters / 4)
    boundaries = sorted(
        {
            Decimal(0),
            route_distance_meters,
            min(first_range, route_distance_meters),
            *(
                min(route_distance_meters, width * i)
                for i in range(
                    1, min(ceil(route_distance_meters / width), max_offers) + 1
                )
            ),
        }
    )
    groups = _progress_groups(offers, boundaries)
    selection.bridges(offers, boundaries, width)
    selection.fill(groups)
    return tuple(
        sorted(
            selection.offers.values(),
            key=lambda v: (
                v.progress_meters,
                v.station.station_id,
            ),
        )
    )


def matrix_points(
    start: Coordinates,
    stations: tuple[ProjectedCandidate, ...],
    finish: Coordinates,
) -> tuple[tuple[Coordinates, ...], tuple[int, ...]]:
    """Give shared-point offers one provider location but separate graph nodes."""
    points = [start]
    indices: dict[Coordinates, int] = {start: 0}
    offer_indices = []
    for item in stations:
        point = item.station.coordinates
        if point == finish:
            # The destination must stay last for graph normalization. Fill its
            # shared offer index once the distinct interior points are known.
            offer_indices.append(-1)
            continue
        index = indices.get(point)
        if index is None:
            index = len(points)
            indices[point] = index
            points.append(point)
        offer_indices.append(index)
    points.append(finish)
    return tuple(points), tuple(
        len(points) - 1 if index == -1 else index for index in offer_indices
    )
