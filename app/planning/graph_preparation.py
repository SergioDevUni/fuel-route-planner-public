from dataclasses import dataclass
from decimal import Decimal

from app.domain.fuel import VehicleSpec
from app.domain.units import finite_nonnegative
from app.planning.fuel_graph import DirectedFuelGraph, SparseDirectedFuelGraph
from app.planning.graph_bounds import (
    all_pairs_lower_bounds,
    remaining_distances,
    remaining_from_rows,
    shortest_from_rows,
)

Outgoing = tuple[tuple[tuple[int, Decimal, Decimal], ...], ...]
Bounds = tuple[Decimal | None, ...]
TimeRows = tuple[tuple[tuple[int, Decimal], ...], ...]


@dataclass(frozen=True)
class PreparedGraph:
    outgoing: Outgoing
    remaining: Bounds
    remaining_time: Bounds | None
    time_bounds: tuple[Bounds, ...] | None
    sparse_time_rows: TimeRows | None
    finish_legs: tuple[tuple[Decimal, Decimal] | None, ...]


def graph_lower_bounds(
    graph: DirectedFuelGraph | SparseDirectedFuelGraph,
    max_total_seconds: Decimal | None,
) -> tuple[Outgoing, Bounds, Bounds | None, tuple[Bounds, ...] | None, TimeRows | None]:
    remaining_time: tuple[Decimal | None, ...] | None
    sparse_time_rows: tuple[tuple[tuple[int, Decimal], ...], ...] | None = None
    if isinstance(graph, SparseDirectedFuelGraph):
        outgoing = graph.outgoing
        remaining = remaining_from_rows(
            tuple(
                tuple((target, distance) for target, distance, _ in row)
                for row in outgoing
            )
        )
        sparse_time_rows = tuple(
            tuple((target, duration) for target, _, duration in row) for row in outgoing
        )
        remaining_time = remaining_from_rows(sparse_time_rows)
        # Large nationwide graphs retain a constant-time global-price bound.
        # Smaller sparse graphs can afford a stronger lazy time-price index.
        time_bounds = None
    else:
        outgoing = tuple(
            tuple(
                (target, distance, duration)
                for target, distance in enumerate(row)
                if target != source
                and distance is not None
                and (
                    duration := (
                        graph.durations_seconds[source][target]
                        if graph.durations_seconds is not None
                        else Decimal(0)
                    )
                )
                is not None
            )
            for source, row in enumerate(graph.distances_meters)
        )
        remaining = remaining_distances(graph.distances_meters)
        remaining_time = (
            remaining_distances(graph.durations_seconds)
            if graph.durations_seconds is not None
            else None
        )
        time_bounds = (
            all_pairs_lower_bounds(graph.durations_seconds)
            if max_total_seconds is not None and graph.durations_seconds is not None
            else None
        )
    return outgoing, remaining, remaining_time, time_bounds, sparse_time_rows


def prune_infeasible_edges(
    outgoing: Outgoing,
    remaining: Bounds,
    remaining_time: Bounds | None,
    initial_gallons: Decimal,
    reserve_gallons: Decimal,
    vehicle: VehicleSpec,
    max_total_meters: Decimal | None,
    max_total_seconds: Decimal | None,
) -> Outgoing:
    # Remove edges that cannot belong to any fuel- and time-feasible trip.
    # Forward/reverse shortest paths are optimistic, so this cannot discard
    # a valid route even when some of those paths revisit stations.
    from_origin_distance = (
        shortest_from_rows(
            tuple(
                tuple((target, distance) for target, distance, _ in row)
                for row in outgoing
            ),
            0,
            max_total_meters,
        )
        if max_total_meters is not None
        else None
    )
    from_origin_time = (
        shortest_from_rows(
            tuple(
                tuple((target, seconds) for target, _, seconds in row)
                for row in outgoing
            ),
            0,
            max_total_seconds,
        )
        if max_total_seconds is not None
        else None
    )
    outgoing = tuple(
        tuple(
            (target, distance, seconds)
            for target, distance, seconds in row
            if vehicle.gallons_for_meters(distance)
            <= (initial_gallons if source == 0 else vehicle.capacity_gallons)
            - reserve_gallons
            and (
                max_total_meters is None
                or (
                    from_origin_distance is not None
                    and (prefix_distance := from_origin_distance[source]) is not None
                    and (suffix_distance := remaining[target]) is not None
                    and prefix_distance + distance + suffix_distance <= max_total_meters
                )
            )
            and (
                max_total_seconds is None
                or (
                    from_origin_time is not None
                    and remaining_time is not None
                    and (prefix_time := from_origin_time[source]) is not None
                    and (suffix_time := remaining_time[target]) is not None
                    and prefix_time + seconds + suffix_time <= max_total_seconds
                )
            )
        )
        for source, row in enumerate(outgoing)
    )
    return outgoing


def prepare_graph(
    graph: DirectedFuelGraph | SparseDirectedFuelGraph,
    initial_gallons: Decimal,
    reserve_gallons: Decimal,
    vehicle: VehicleSpec,
    max_total_meters: Decimal | None,
    max_total_seconds: Decimal | None,
    max_expansions: int,
) -> PreparedGraph:
    finite_nonnegative(initial_gallons, "initial_gallons")
    finite_nonnegative(reserve_gallons, "reserve_gallons")
    if not reserve_gallons <= initial_gallons <= vehicle.capacity_gallons:
        raise ValueError("initial fuel must fit reserve and capacity")
    if reserve_gallons >= vehicle.capacity_gallons or max_expansions < 1:
        raise ValueError("reserve or expansion limit is invalid")
    if max_total_meters is not None:
        finite_nonnegative(max_total_meters, "max_total_meters")
    if max_total_seconds is not None:
        finite_nonnegative(max_total_seconds, "max_total_seconds")
        if isinstance(graph, DirectedFuelGraph) and graph.durations_seconds is None:
            raise ValueError("time-limited search needs road durations")
    outgoing, remaining, remaining_time, time_bounds, sparse_time_rows = (
        graph_lower_bounds(graph, max_total_seconds)
    )
    outgoing = prune_infeasible_edges(
        outgoing,
        remaining,
        remaining_time,
        initial_gallons,
        reserve_gallons,
        vehicle,
        max_total_meters,
        max_total_seconds,
    )
    finish_legs = tuple(
        {target: (distance, duration) for target, distance, duration in row}.get(
            len(graph.stations) + 1
        )
        for row in outgoing
    )
    return PreparedGraph(
        outgoing, remaining, remaining_time, time_bounds, sparse_time_rows, finish_legs
    )
