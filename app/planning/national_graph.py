"""Join cached nationwide station legs with trip-specific local road access.

The SQLite cache contains road facts, never prices. CSV offer IDs and prices
are supplied at query time, so co-located offers remain separate choices.
"""

import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from app.domain.fuel import FuelOpportunity, VehicleSpec
from app.domain.routing import Coordinates
from app.domain.units import METERS_PER_MILE
from app.planning.budget import RequestBudget
from app.planning.candidates import StationCandidate
from app.planning.endpoint_block_cache import EndpointBlockCache
from app.planning.fuel_graph import DirectedFuelGraph, SparseDirectedFuelGraph
from app.planning.road_cache_identity import SCHEMA_VERSION as UPDATE_SAFE_SCHEMA
from app.routing.geometry import geodesic_meters
from app.routing.local_valhalla import LocalRoadBlock, LocalValhalla


class StationRoadGraphUnavailable(ValueError):
    pass


def completed_road_points(
    database: Path,
    candidate_version: str,
    graph_version: str,
    *,
    active_point_hash: str | None = None,
) -> set[Coordinates]:
    """Read only source-complete road points from a matching checkpoint."""
    if not database.is_file():
        raise StationRoadGraphUnavailable("station road cache is missing")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        if not _matches_road_cache(
            metadata, candidate_version, graph_version, active_point_hash
        ):
            raise StationRoadGraphUnavailable("station road cache version differs")
        active_clause = (
            "WHERE point.active = 1"
            if metadata.get("schema") == UPDATE_SAFE_SCHEMA
            else ""
        )
        return {
            Coordinates(latitude, longitude)
            for latitude, longitude in connection.execute(
                "SELECT latitude, longitude FROM point "
                f"JOIN complete_source USING(point_id) {active_clause}"
            )
        }
    finally:
        connection.close()


def _reserve_verification_time(budget: RequestBudget) -> None:
    if budget.remaining_seconds <= 6:
        raise StationRoadGraphUnavailable("insufficient time for local road query")


def _matches_road_cache(
    metadata: dict[str, str],
    candidate_version: str,
    graph_version: str,
    active_point_hash: str | None,
) -> bool:
    if metadata.get("road_graph_version") != graph_version:
        return False
    if metadata.get("schema") == UPDATE_SAFE_SCHEMA:
        return (
            bool(active_point_hash)
            and metadata.get("active_point_hash") == active_point_hash
        )
    return (
        metadata.get("schema") == "csv-station-edges-v1-auto"
        and metadata.get("candidate_hash") == candidate_version
    )


@dataclass(frozen=True, slots=True)
class NationalRoadSelection:
    graph: DirectedFuelGraph | SparseDirectedFuelGraph
    retained: tuple[StationCandidate, ...]
    unavailable_ids: tuple[str, ...]


def _chunks(items: tuple[int, ...], size: int) -> tuple[tuple[int, ...], ...]:
    return tuple(items[offset : offset + size] for offset in range(0, len(items), size))


def _endpoint_batches(
    point_ids: tuple[int, ...],
    coordinates: dict[int, Coordinates],
    size: int,
) -> tuple[tuple[int, ...], ...]:
    """Keep each endpoint matrix's targets geographically close together."""
    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    for point_id in point_ids:
        point = coordinates[point_id]
        cells[(int(point.latitude // 5), int(point.longitude // 5))].append(point_id)
    return tuple(
        batch
        for key in sorted(cells)
        for batch in _chunks(tuple(sorted(cells[key])), size)
    )


def build_trip_road_graph(
    database: Path,
    stations: tuple[StationCandidate, ...],
    start: Coordinates,
    finish: Coordinates,
    local: LocalValhalla,
    budget: RequestBudget,
    *,
    candidate_version: str,
    active_point_hash: str | None = None,
    initial_gallons: Decimal,
    reserve_gallons: Decimal,
    vehicle: VehicleSpec | None = None,
    max_station_snap_meters: Decimal = Decimal(1000),
    endpoint_cache: EndpointBlockCache | None = None,
    sparse: bool = False,
) -> NationalRoadSelection:
    """Load road facts, connect endpoints, and assemble distinct CSV offers."""
    vehicle = vehicle or VehicleSpec()
    if not reserve_gallons <= initial_gallons <= vehicle.capacity_gallons:
        raise ValueError("initial fuel must fit reserve and tank")
    if len({station.station_id for station in stations}) != len(stations):
        raise ValueError("CSV offer IDs must be distinct")
    if not database.is_file():
        raise StationRoadGraphUnavailable("nationwide station road cache is missing")
    with closing(
        sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    ) as connection:
        checkpoint = _load_checkpoint(
            connection,
            stations,
            candidate_version,
            local.graph_version,
            active_point_hash,
            max_station_snap_meters,
        )
        assembly = RoadGraphAssembly(len(checkpoint.retained) + 2, sparse)
        _cached_legs(connection, checkpoint, assembly)
    _endpoint_legs(
        checkpoint,
        assembly,
        start,
        finish,
        local,
        budget,
        initial_gallons,
        reserve_gallons,
        vehicle,
        max_station_snap_meters,
        endpoint_cache,
    )
    graph = _finish_graph(
        checkpoint.retained, assembly, candidate_version, local.graph_version
    )
    return NationalRoadSelection(graph, checkpoint.retained, checkpoint.unavailable)


@dataclass(frozen=True)
class RoadCheckpoint:
    retained: tuple[StationCandidate, ...]
    point_ids: tuple[int, ...]
    coordinates: dict[int, Coordinates]
    unavailable: tuple[str, ...]


@dataclass
class RoadGraphAssembly:
    size: int
    sparse: bool
    distances: list[list[Decimal | None]] = field(init=False)
    durations: list[list[Decimal | None]] = field(init=False)
    sparse_rows: list[dict[int, tuple[Decimal, Decimal]]] = field(init=False)
    by_point: dict[int, list[int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.distances = (
            [] if self.sparse else [[None] * self.size for _ in range(self.size)]
        )
        self.durations = (
            [] if self.sparse else [[None] * self.size for _ in range(self.size)]
        )
        self.sparse_rows = [{} for _ in range(self.size)] if self.sparse else []

    def set_leg(
        self, source: int, target: int, meters: Decimal, seconds: Decimal
    ) -> None:
        if self.sparse:
            if source != target:
                self.sparse_rows[source][target] = (meters, seconds)
        else:
            self.distances[source][target], self.durations[source][target] = (
                meters,
                seconds,
            )


def _load_checkpoint(
    connection: sqlite3.Connection,
    stations: tuple[StationCandidate, ...],
    candidate_version: str,
    graph_version: str,
    active_point_hash: str | None,
    max_station_snap_meters: Decimal,
) -> RoadCheckpoint:
    metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    if not _matches_road_cache(
        metadata, candidate_version, graph_version, active_point_hash
    ):
        raise StationRoadGraphUnavailable("station road cache version differs")
    active_clause = (
        " WHERE active = 1" if metadata.get("schema") == UPDATE_SAFE_SCHEMA else ""
    )
    known = {
        (latitude, longitude): (point_id, snap, error)
        for point_id, latitude, longitude, snap, error in connection.execute(
            "SELECT point_id, latitude, longitude, snap_meters, snap_error "
            f"FROM point{active_clause}"
        )
    }
    retained: list[StationCandidate] = []
    point_ids: list[int] = []
    point_coordinates: dict[int, Coordinates] = {}
    unavailable: list[str] = []
    for station in stations:
        point = station.coordinates
        record = known.get((point.latitude, point.longitude))
        if record is None:
            raise StationRoadGraphUnavailable(
                f"CSV point for ID {station.station_id} is absent"
            )
        point_id, snap, error = record
        if snap is None and error is None:
            raise StationRoadGraphUnavailable(
                f"CSV point for ID {station.station_id} is not road-checked"
            )
        allowed_snap = (
            max_station_snap_meters
            if station.location_precision == "city_representative"
            else min(max_station_snap_meters, Decimal(250))
        )
        if snap is None or Decimal(str(snap)) > allowed_snap:
            unavailable.append(station.station_id)
            continue
        retained.append(station)
        point_ids.append(point_id)
        point_coordinates[point_id] = point
    unique_ids = tuple(sorted(set(point_ids)))
    complete = {
        point_id
        for batch in _chunks(unique_ids, 500)
        for (point_id,) in connection.execute(
            f"SELECT point_id FROM complete_source WHERE point_id IN "
            f"({','.join('?' for _ in batch)})",
            batch,
        )
    }
    if complete != set(unique_ids):
        raise StationRoadGraphUnavailable("selected station road legs are incomplete")

    return RoadCheckpoint(
        tuple(retained), tuple(point_ids), point_coordinates, tuple(unavailable)
    )


def _cached_legs(
    connection: sqlite3.Connection,
    checkpoint: RoadCheckpoint,
    assembly: RoadGraphAssembly,
) -> None:
    point_ids, unique_ids = (
        checkpoint.point_ids,
        tuple(sorted(set(checkpoint.point_ids))),
    )
    by_point: dict[int, list[int]] = {}
    for node, point_id in enumerate(point_ids, start=1):
        by_point.setdefault(point_id, []).append(node)
    for node in range(assembly.size):
        if not assembly.sparse:
            assembly.set_leg(node, node, Decimal(0), Decimal(0))
    for co_located in by_point.values():
        for source in co_located:
            for target in co_located:
                assembly.set_leg(source, target, Decimal(0), Decimal(0))
    for batch in _chunks(unique_ids, 500):
        query = (
            "SELECT source_id, target_id, distance_meters, duration_seconds "
            f"FROM edge WHERE source_id IN ({','.join('?' for _ in batch)})"
        )
        for source_id, target_id, meters, seconds in connection.execute(query, batch):
            targets = by_point.get(target_id)
            if targets is None:
                continue
            for source_node in by_point[source_id]:
                for target_node in targets:
                    assembly.set_leg(
                        source_node,
                        target_node,
                        Decimal(str(meters)),
                        Decimal(str(seconds)),
                    )
    assembly.by_point = by_point


def _endpoint_legs(
    checkpoint: RoadCheckpoint,
    assembly: RoadGraphAssembly,
    start: Coordinates,
    finish: Coordinates,
    local: LocalValhalla,
    budget: RequestBudget,
    initial_gallons: Decimal,
    reserve_gallons: Decimal,
    vehicle: VehicleSpec,
    max_station_snap_meters: Decimal,
    endpoint_cache: EndpointBlockCache | None,
) -> None:
    unique_ids = tuple(sorted(set(checkpoint.point_ids)))
    point_coordinates, by_point = checkpoint.coordinates, assembly.by_point
    final = assembly.size - 1
    _reserve_verification_time(budget)
    endpoint_snaps = local.locate((start, finish), budget)
    if any(snap > max_station_snap_meters for snap in endpoint_snaps):
        raise StationRoadGraphUnavailable("trip endpoint cannot reach road graph")
    first_range = (
        (initial_gallons - reserve_gallons) * vehicle.miles_per_gallon * METERS_PER_MILE
    )
    later_range = (
        (vehicle.capacity_gallons - reserve_gallons)
        * vehicle.miles_per_gallon
        * METERS_PER_MILE
    )
    # Requested coordinates can move towards each other when snapped. A raw
    # geodesic larger than range is not a safe exclusion without that allowance.
    point_allowances = {
        point_id: max(
            max_station_snap_meters
            if checkpoint.retained[node - 1].location_precision == "city_representative"
            else min(max_station_snap_meters, Decimal(250))
            for node in by_point[point_id]
        )
        for point_id in unique_ids
    }
    first_points = tuple(
        point_id
        for point_id in unique_ids
        if geodesic_meters(start, point_coordinates[point_id])
        <= first_range + max_station_snap_meters + point_allowances[point_id]
    )
    final_points = tuple(
        point_id
        for point_id in unique_ids
        if geodesic_meters(point_coordinates[point_id], finish)
        <= later_range + max_station_snap_meters + point_allowances[point_id]
    )
    # Spatial batches avoid spreading a long matrix across distant targets.
    # Larger one-to-many batches also avoid repeating the endpoint search for
    # every group of five stations on a cold trip.
    batch_size = min(local.max_points, 20)
    endpoint_requests = [
        ("first", batch)
        for batch in _endpoint_batches(first_points, point_coordinates, batch_size)
    ] + [
        ("final", batch)
        for batch in _endpoint_batches(final_points, point_coordinates, batch_size)
    ]

    endpoint_matrix = endpoint_cache or local

    def fetch_endpoint(
        request: tuple[str, tuple[int, ...]],
    ) -> LocalRoadBlock:
        kind, batch_points = request
        if kind == "first":
            return endpoint_matrix.matrix_block(
                (start,),
                tuple(point_coordinates[point_id] for point_id in batch_points),
                budget,
            )
        return endpoint_matrix.matrix_block(
            tuple(point_coordinates[point_id] for point_id in batch_points),
            (finish,),
            budget,
        )

    if endpoint_requests:
        _reserve_verification_time(budget)
        with ThreadPoolExecutor(max_workers=min(4, len(endpoint_requests))) as executor:
            for (kind, batch_points), block in zip(
                endpoint_requests,
                executor.map(fetch_endpoint, endpoint_requests),
                strict=True,
            ):
                for index, point_id in enumerate(batch_points):
                    if kind == "first":
                        meters = block.distances_meters[0][index]
                        seconds = block.durations_seconds[0][index]
                        if meters is not None and meters <= first_range:
                            for node in by_point[point_id]:
                                assert seconds is not None
                                assembly.set_leg(0, node, meters, seconds)
                    else:
                        meters = block.distances_meters[index][0]
                        seconds = block.durations_seconds[index][0]
                        if meters is not None and meters <= later_range:
                            for node in by_point[point_id]:
                                assert seconds is not None
                                assembly.set_leg(node, final, meters, seconds)
    if geodesic_meters(start, finish) <= first_range + 2 * max_station_snap_meters:
        _reserve_verification_time(budget)
        direct = endpoint_matrix.matrix_block((start,), (finish,), budget)
        meters = direct.distances_meters[0][0]
        if meters is not None and meters <= first_range:
            seconds = direct.durations_seconds[0][0]
            assert seconds is not None
            assembly.set_leg(0, final, meters, seconds)


def _finish_graph(
    retained: tuple[StationCandidate, ...],
    assembly: RoadGraphAssembly,
    candidate_version: str,
    graph_version: str,
) -> DirectedFuelGraph | SparseDirectedFuelGraph:
    offers = tuple(
        FuelOpportunity(station.station_id, station.price_per_gallon)
        for station in retained
    )
    version = f"national:{candidate_version}:{graph_version}"
    graph: DirectedFuelGraph | SparseDirectedFuelGraph
    if assembly.sparse:
        graph = SparseDirectedFuelGraph(
            offers,
            tuple(
                tuple(
                    (target, meters, seconds)
                    for target, (meters, seconds) in sorted(row.items())
                )
                for row in assembly.sparse_rows
            ),
            version,
        )
    else:
        graph = DirectedFuelGraph(
            stations=offers,
            distances_meters=tuple(tuple(row) for row in assembly.distances),
            durations_seconds=tuple(tuple(row) for row in assembly.durations),
            version=version,
        )
    return graph
