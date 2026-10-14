"""Sparse national legs preserve the dense fuel-search answer."""

from decimal import Decimal

from app.domain.fuel import FuelOpportunity
from app.domain.units import METERS_PER_MILE
from app.planning.fuel_graph import DirectedFuelGraph, SparseDirectedFuelGraph
from app.planning.graph_search import search_fuel_graph


def test_sparse_and_dense_search_agree_on_directed_time_limited_graph() -> None:
    stations = (
        FuelOpportunity("a", Decimal("4.1")),
        FuelOpportunity("b", Decimal("2.9")),
        FuelOpportunity("c", Decimal("3.4")),
    )
    size = len(stations) + 2
    distances: list[list[Decimal | None]] = [[None] * size for _ in range(size)]
    durations: list[list[Decimal | None]] = [[None] * size for _ in range(size)]
    edges = (
        (0, 1, 90_000, 3_000),
        (0, 2, 110_000, 3_600),
        (1, 2, 90_000, 3_000),
        (1, 3, 95_000, 3_000),
        (2, 3, 80_000, 2_700),
        (2, 4, 150_000, 5_000),
        (3, 4, 100_000, 3_400),
    )
    sparse_rows: list[list[tuple[int, Decimal, Decimal]]] = [[] for _ in range(size)]
    for source, target, meters, seconds in edges:
        distances[source][target] = Decimal(meters)
        durations[source][target] = Decimal(seconds)
        sparse_rows[source].append((target, Decimal(meters), Decimal(seconds)))
    dense = DirectedFuelGraph(
        stations,
        tuple(tuple(row) for row in distances),
        "dense",
        tuple(tuple(row) for row in durations),
    )
    sparse = SparseDirectedFuelGraph(
        stations,
        tuple(tuple(row) for row in sparse_rows),
        "sparse",
    )
    options = dict(max_total_seconds=Decimal(12_000))
    dense_result = search_fuel_graph(dense, Decimal(6), **options)
    sparse_result = search_fuel_graph(sparse, Decimal(6), **options)
    assert dense_result.complete and sparse_result.complete
    assert dense_result.solution is not None
    assert sparse_result.solution is not None
    assert sparse_result.solution.station_ids == dense_result.solution.station_ids
    assert sparse_result.solution.purchase_cost == dense_result.solution.purchase_cost


def test_sparse_search_handles_more_than_two_hundred_road_points() -> None:
    size = 222
    leg = Decimal(10) * METERS_PER_MILE
    graph = SparseDirectedFuelGraph(
        tuple(FuelOpportunity(str(index), Decimal(2)) for index in range(size - 2)),
        tuple(
            ((source + 1, leg, Decimal(600)),) if source < size - 1 else ()
            for source in range(size)
        ),
        "long-chain",
    )
    result = search_fuel_graph(
        graph,
        Decimal(50),
        max_total_seconds=Decimal(600 * (size - 1)),
    )
    assert result.complete
    assert result.solution is not None
    assert len(result.solution.station_ids) == size - 2
