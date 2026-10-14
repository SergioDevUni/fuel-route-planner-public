from decimal import Decimal

from app.domain.fuel import FuelOpportunity
from app.domain.units import METERS_PER_MILE
from app.planning.fuel_graph import DirectedFuelGraph


def miles(value: int) -> Decimal:
    return Decimal(value) * METERS_PER_MILE


def graph(
    stations: tuple[tuple[str, str], ...],
    edges: dict[tuple[int, int], int],
) -> DirectedFuelGraph:
    size = len(stations) + 2
    rows: list[list[Decimal | None]] = [[None] * size for _ in range(size)]
    for (source, target), distance in edges.items():
        rows[source][target] = miles(distance)
    return DirectedFuelGraph(
        tuple(FuelOpportunity(name, Decimal(price)) for name, price in stations),
        tuple(tuple(row) for row in rows),
        "synthetic-directed-v1",
    )
