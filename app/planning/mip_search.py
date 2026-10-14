"""Mixed-integer fuel and time search on a declared directed station graph.

Unlike simple-path enumeration, the number of decision variables grows with
road edges. This solves purchase quantities and stop sequence together. It is
exact for a complete supplied graph when HiGHS reports optimal completion;
the road graph and its station scope remain separate responsibilities.
"""

import heapq
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
from scipy.optimize import (  # type: ignore[import-untyped]
    Bounds,
    LinearConstraint,
    milp,
)
from scipy.sparse import coo_matrix  # type: ignore[import-untyped]

from app.domain.fuel import VehicleSpec
from app.domain.units import finite_nonnegative
from app.planning.fuel import FuelShortfall, optimize_fuel
from app.planning.fuel_graph import (
    DirectedFuelGraph,
    GraphFuelSolution,
    GraphSearchResult,
)


@dataclass(frozen=True, slots=True)
class _Arc:
    source: int
    target: int
    meters: Decimal
    seconds: Decimal


def _shortest_bound(
    arcs: list[_Arc], size: int, terminal: int, *, reverse: bool, time: bool
) -> list[Decimal | None]:
    """Optimistic directed travel bound, ignoring purchases and stop sequence."""
    adjacent: list[list[tuple[int, Decimal]]] = [[] for _ in range(size)]
    for arc in arcs:
        source, target = (
            (arc.target, arc.source) if reverse else (arc.source, arc.target)
        )
        adjacent[source].append((target, arc.seconds if time else arc.meters))
    shortest: list[Decimal | None] = [None] * size
    shortest[terminal] = Decimal(0)
    heap = [(Decimal(0), terminal)]
    while heap:
        current, node = heapq.heappop(heap)
        if current != shortest[node]:
            continue
        for neighbor, length in adjacent[node]:
            proposal = current + length
            previous = shortest[neighbor]
            if previous is None or proposal < previous:
                shortest[neighbor] = proposal
                heapq.heappush(heap, (proposal, neighbor))
    return shortest


def _within_bound(
    arc: _Arc,
    before: list[Decimal | None],
    after: list[Decimal | None],
    limit: Decimal,
    *,
    time: bool,
) -> bool:
    prefix = before[arc.source]
    suffix = after[arc.target]
    return (
        prefix is not None
        and suffix is not None
        and prefix + (arc.seconds if time else arc.meters) + suffix <= limit
    )


def search_fuel_graph_mip(
    graph: DirectedFuelGraph,
    initial_gallons: Decimal,
    reserve_gallons: Decimal = Decimal(0),
    *,
    max_total_meters: Decimal | None = None,
    max_total_seconds: Decimal | None = None,
    max_solver_seconds: float = 8,
    vehicle: VehicleSpec | None = None,
) -> GraphSearchResult:
    """Solve fuel purchases and route edges jointly with continuous gallons."""
    vehicle = vehicle or VehicleSpec()
    finite_nonnegative(initial_gallons, "initial_gallons")
    finite_nonnegative(reserve_gallons, "reserve_gallons")
    if not reserve_gallons <= initial_gallons <= vehicle.capacity_gallons:
        raise ValueError("initial fuel must fit reserve and capacity")
    if reserve_gallons >= vehicle.capacity_gallons or max_solver_seconds <= 0:
        raise ValueError("invalid reserve or solver time")
    if max_total_meters is not None:
        finite_nonnegative(max_total_meters, "max_total_meters")
    if max_total_seconds is not None:
        finite_nonnegative(max_total_seconds, "max_total_seconds")
        if graph.durations_seconds is None:
            raise ValueError("time-limited search needs road durations")

    size = len(graph.stations) + 2
    destination = size - 1
    arcs: list[_Arc] = []
    for source, row in enumerate(graph.distances_meters[:-1]):
        for target in range(1, size):
            distance = row[target]
            if distance is None or source == target:
                continue
            consumption = vehicle.gallons_for_meters(distance)
            available = (
                initial_gallons if source == 0 else vehicle.capacity_gallons
            ) - reserve_gallons
            if consumption > available:
                continue
            seconds = (
                graph.durations_seconds[source][target]
                if graph.durations_seconds is not None
                else Decimal(0)
            )
            if seconds is None:
                continue
            arcs.append(_Arc(source, target, distance, seconds))
    for limit, time in (
        (max_total_seconds, True),
        (max_total_meters, False),
    ):
        if limit is None:
            continue
        before = _shortest_bound(arcs, size, 0, reverse=False, time=time)
        after = _shortest_bound(arcs, size, destination, reverse=True, time=time)
        arcs = [
            arc for arc in arcs if _within_bound(arc, before, after, limit, time=time)
        ]
    if not arcs:
        return GraphSearchResult(None, True, 0, graph.version)

    edge_count = len(arcs)
    fuel_start = edge_count
    purchase_start = fuel_start + size
    variables = purchase_start + len(graph.stations)
    incoming: list[list[int]] = [[] for _ in range(size)]
    outgoing: list[list[int]] = [[] for _ in range(size)]
    for edge_index, arc in enumerate(arcs):
        outgoing[arc.source].append(edge_index)
        incoming[arc.target].append(edge_index)
    if not outgoing[0] or not incoming[destination]:
        return GraphSearchResult(None, True, 0, graph.version)

    objective = np.zeros(variables)
    lower = np.zeros(variables)
    upper = np.ones(variables) * np.inf
    integrality = np.zeros(variables, dtype=np.int32)
    upper[:edge_count] = 1
    integrality[:edge_count] = 1
    capacity = float(vehicle.capacity_gallons)
    upper[fuel_start:purchase_start] = capacity
    lower[fuel_start] = upper[fuel_start] = float(initial_gallons)
    upper[purchase_start:] = capacity
    for index, station in enumerate(graph.stations):
        objective[purchase_start + index] = float(station.price_per_gallon)

    coordinates: list[tuple[int, int, float]] = []
    row_lowers: list[float] = []
    row_uppers: list[float] = []

    def constraint(coefficients: dict[int, float], low: float, high: float) -> None:
        row = len(row_lowers)
        coordinates.extend(
            (row, column, value) for column, value in coefficients.items() if value != 0
        )
        row_lowers.append(low)
        row_uppers.append(high)

    constraint({edge_index: 1 for edge_index in outgoing[0]}, 1, 1)
    constraint({edge_index: 1 for edge_index in incoming[destination]}, 1, 1)
    for node in range(1, destination):
        visits = {edge_index: 1.0 for edge_index in incoming[node]}
        balance = visits.copy()
        for edge_index in outgoing[node]:
            balance[edge_index] = balance.get(edge_index, 0) - 1
        constraint(balance, 0, 0)
        constraint(visits, 0, 1)
        fuel_index = fuel_start + node
        purchase = purchase_start + node - 1
        constraint(
            {fuel_index: 1, **{edge_index: -capacity for edge_index in incoming[node]}},
            -np.inf,
            0,
        )
        constraint(
            {
                fuel_index: -1,
                **{edge_index: float(reserve_gallons) for edge_index in incoming[node]},
            },
            -np.inf,
            0,
        )
        constraint(
            {purchase: 1, **{edge_index: -capacity for edge_index in incoming[node]}},
            -np.inf,
            0,
        )
        constraint(
            {
                fuel_index: 1,
                purchase: 1,
                **{edge_index: -capacity for edge_index in incoming[node]},
            },
            -np.inf,
            0,
        )
    constraint({fuel_start + destination: 1}, float(reserve_gallons), np.inf)

    big_m = 2 * capacity
    for edge_index, arc in enumerate(arcs):
        used = float(vehicle.gallons_for_meters(arc.meters))
        fuel_balance = {
            fuel_start + arc.target: 1,
            fuel_start + arc.source: -1,
        }
        if arc.source:
            fuel_balance[purchase_start + arc.source - 1] = -1
        constraint({**fuel_balance, edge_index: big_m}, -np.inf, big_m - used)
        constraint({**fuel_balance, edge_index: -big_m}, -big_m - used, np.inf)
    if max_total_seconds is not None:
        constraint(
            {index: float(arc.seconds) for index, arc in enumerate(arcs)},
            -np.inf,
            float(max_total_seconds),
        )
    if max_total_meters is not None:
        constraint(
            {index: float(arc.meters) for index, arc in enumerate(arcs)},
            -np.inf,
            float(max_total_meters),
        )
    rows = np.array([item[0] for item in coordinates])
    columns = np.array([item[1] for item in coordinates])
    values = np.array([item[2] for item in coordinates])
    matrix = coo_matrix(
        (values, (rows, columns)), shape=(len(row_lowers), variables)
    ).tocsr()
    result = milp(
        objective,
        integrality=integrality,
        bounds=Bounds(lower, upper),
        constraints=LinearConstraint(matrix, row_lowers, row_uppers),
        options={"time_limit": max_solver_seconds, "mip_rel_gap": 0.0},
    )
    complete = result.status in (0, 2)
    nodes = int(getattr(result, "mip_node_count", 0) or 0)
    if result.x is None:
        return GraphSearchResult(None, complete, nodes, graph.version)
    selected = [index for index in range(edge_count) if result.x[index] > 0.5]
    by_source = {arcs[index].source: arcs[index] for index in selected}
    ordered: list[_Arc] = []
    current = 0
    visited: set[int] = set()
    while current != destination:
        if current in visited or current not in by_source:
            return GraphSearchResult(None, False, nodes, graph.version)
        visited.add(current)
        arc = by_source[current]
        ordered.append(arc)
        current = arc.target
    station_ids = tuple(
        graph.stations[arc.target - 1].station_id
        for arc in ordered
        if arc.target != destination
    )
    opportunities = tuple(
        graph.stations[arc.target - 1] for arc in ordered if arc.target != destination
    )
    legs = tuple(arc.meters for arc in ordered)
    try:
        fuel_plan = optimize_fuel(
            opportunities, legs, initial_gallons, reserve_gallons, vehicle
        )
    except FuelShortfall:
        return GraphSearchResult(None, False, nodes, graph.version)
    return GraphSearchResult(
        GraphFuelSolution(station_ids, legs, fuel_plan, sum(legs, Decimal(0))),
        complete,
        nodes,
        graph.version,
    )
