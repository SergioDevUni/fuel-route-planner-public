"""Bind a bounded directed road matrix to distinct CSV fuel offers."""

from decimal import Decimal

from app.domain.fuel import FuelOpportunity
from app.domain.road import RoadDistanceMatrix
from app.domain.units import finite_nonnegative
from app.planning.candidates import StationCandidate
from app.planning.fuel_graph import DirectedFuelGraph


def graph_from_matrix(
    stations: tuple[StationCandidate, ...],
    matrix: RoadDistanceMatrix,
    version: str,
    *,
    station_point_indices: tuple[int, ...],
    max_station_snap_meters: Decimal = Decimal(250),
    max_endpoint_snap_meters: Decimal = Decimal(1000),
) -> DirectedFuelGraph:
    """Keep safe-snap stations and their directed road legs in matrix order."""
    size = len(matrix.distances_meters)
    if (
        size < 2
        or any(len(row) != size for row in matrix.distances_meters)
        or (
            matrix.durations_seconds is not None
            and (
                len(matrix.durations_seconds) != size
                or any(len(row) != size for row in matrix.durations_seconds)
            )
        )
        or len(matrix.source_snap_meters) != size
        or len(matrix.destination_snap_meters) != size
        or len(station_point_indices) != len(stations)
        or any(index < 0 or index >= size for index in station_point_indices)
    ):
        raise ValueError("road matrix does not match station point indices")
    finite_nonnegative(max_station_snap_meters, "station snap limit")
    finite_nonnegative(max_endpoint_snap_meters, "endpoint snap limit")
    for snaps in (matrix.source_snap_meters, matrix.destination_snap_meters):
        for snap in snaps:
            finite_nonnegative(snap, "road snap")
        if snaps[0] > max_endpoint_snap_meters or snaps[-1] > max_endpoint_snap_meters:
            raise ValueError("road matrix endpoint snap exceeds policy")
    retained = tuple(
        (offer_index, point_index)
        for offer_index, point_index in enumerate(station_point_indices)
        if matrix.source_snap_meters[point_index] <= max_station_snap_meters
        and matrix.destination_snap_meters[point_index] <= max_station_snap_meters
    )
    indices = (0, *(point_index for _, point_index in retained), size - 1)
    return DirectedFuelGraph(
        stations=tuple(
            FuelOpportunity(
                stations[offer_index].station_id,
                stations[offer_index].price_per_gallon,
            )
            for offer_index, _ in retained
        ),
        distances_meters=tuple(
            tuple(matrix.distances_meters[source][target] for target in indices)
            for source in indices
        ),
        version=version,
        durations_seconds=(
            tuple(
                tuple(matrix.durations_seconds[source][target] for target in indices)
                for source in indices
            )
            if matrix.durations_seconds is not None
            else None
        ),
    )
