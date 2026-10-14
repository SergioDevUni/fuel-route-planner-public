"""Fuel graph for directed fuel planning."""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.fuel import FuelOpportunity, FuelPlan
from app.domain.units import finite_nonnegative


@dataclass(frozen=True, slots=True)
class DirectedFuelGraph:
    """Node order: origin, stations, destination."""

    stations: tuple[FuelOpportunity, ...]
    distances_meters: tuple[tuple[Decimal | None, ...], ...]
    version: str
    durations_seconds: tuple[tuple[Decimal | None, ...], ...] | None = None

    def __post_init__(self) -> None:
        size = len(self.stations) + 2
        if not self.version or len(self.distances_meters) != size:
            raise ValueError("graph version and square matrix are required")
        if len({station.station_id for station in self.stations}) != len(self.stations):
            raise ValueError("station IDs must be unique")
        for row in self.distances_meters:
            if len(row) != size:
                raise ValueError("road matrix must be square")
            for distance in row:
                if distance is not None:
                    finite_nonnegative(distance, "road distance")
        if self.durations_seconds is not None:
            if len(self.durations_seconds) != size:
                raise ValueError("duration matrix must be square")
            for distance_row, duration_row in zip(
                self.distances_meters, self.durations_seconds, strict=True
            ):
                if len(duration_row) != size:
                    raise ValueError("duration matrix must be square")
                for distance, duration in zip(distance_row, duration_row, strict=True):
                    if (distance is None) != (duration is None):
                        raise ValueError("road distance and duration disagree")
                    if duration is not None:
                        finite_nonnegative(duration, "road duration")


@dataclass(frozen=True, slots=True)
class SparseDirectedFuelGraph:
    """Directed road legs without allocating a square station matrix."""

    stations: tuple[FuelOpportunity, ...]
    outgoing: tuple[tuple[tuple[int, Decimal, Decimal], ...], ...]
    version: str

    def __post_init__(self) -> None:
        size = len(self.stations) + 2
        if not self.version or len(self.outgoing) != size:
            raise ValueError("graph version and adjacency rows are required")
        if len({station.station_id for station in self.stations}) != len(self.stations):
            raise ValueError("station IDs must be unique")
        for source, row in enumerate(self.outgoing):
            seen: set[int] = set()
            for target, distance, duration in row:
                if not 0 <= target < size or target == source or target in seen:
                    raise ValueError("invalid or repeated road target")
                seen.add(target)
                finite_nonnegative(distance, "road distance")
                finite_nonnegative(duration, "road duration")


@dataclass(frozen=True, slots=True)
class GraphFuelSolution:
    station_ids: tuple[str, ...]
    leg_distances_meters: tuple[Decimal, ...]
    fuel: FuelPlan
    total_distance_meters: Decimal

    @property
    def purchase_cost(self) -> Decimal:
        return self.fuel.exact_purchase_cost


@dataclass(frozen=True, slots=True)
class GraphSearchResult:
    solution: GraphFuelSolution | None
    complete: bool
    expansions: int
    graph_version: str
    labels_pruned: int = 0
