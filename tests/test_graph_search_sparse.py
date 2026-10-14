"""A large sparse chain uses directed time bounds without losing exactness."""

from decimal import Decimal

from app.domain.fuel import FuelOpportunity
from app.domain.units import METERS_PER_MILE
from app.planning.fuel_graph import DirectedFuelGraph
from app.planning.graph_search import search_fuel_graph


def test_sparse_chain_over_sixty_nodes() -> None:
    size = 62
    distances: list[list[Decimal | None]] = [[None] * size for _ in range(size)]
    durations: list[list[Decimal | None]] = [[None] * size for _ in range(size)]
    for index in range(size - 1):
        distances[index][index + 1] = Decimal(10) * METERS_PER_MILE
        durations[index][index + 1] = Decimal(600)
    graph = DirectedFuelGraph(
        tuple(FuelOpportunity(str(index), Decimal(2)) for index in range(size - 2)),
        tuple(tuple(row) for row in distances),
        "sparse-chain",
        tuple(tuple(row) for row in durations),
    )
    result = search_fuel_graph(
        graph,
        Decimal(50),
        max_total_seconds=Decimal(61 * 600),
    )
    assert result.complete
    assert result.solution is not None
    assert result.solution.purchase_cost == Decimal(22)
    assert len(result.solution.station_ids) == size - 2
