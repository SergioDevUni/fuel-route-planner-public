"""Compare graph search with independent path enumeration and fuel LP."""

import itertools
import random
from decimal import Decimal

from app.domain.fuel import FuelOpportunity
from app.domain.units import METERS_PER_MILE
from app.planning.fuel_graph import DirectedFuelGraph
from app.planning.graph_search import search_fuel_graph
from tests.support.oracle import independent_path_cost


def test_random_small_graphs_match_enumerated_lp_oracle() -> None:
    rng = random.Random(8942)
    for _ in range(18):
        count = 3
        prices = tuple(rng.randint(1, 6) for _ in range(count))
        initial = rng.randint(15, 50)
        size = count + 2
        distances: list[list[Decimal | None]] = [[None] * size for _ in range(size)]
        road_miles = {}
        for source in range(size - 1):
            for target in range(1, size):
                if source == target or rng.random() < 0.2:
                    continue
                length = rng.randint(50, 490)
                road_miles[source, target] = length
                distances[source][target] = Decimal(length) * METERS_PER_MILE
        graph = DirectedFuelGraph(
            tuple(
                FuelOpportunity(str(index), Decimal(price))
                for index, price in enumerate(prices)
            ),
            tuple(tuple(row) for row in distances),
            "oracle-random",
        )
        best = None
        for stop_count in range(count + 1):
            for stops in itertools.permutations(range(1, count + 1), stop_count):
                nodes = (0, *stops, size - 1)
                edges = list(zip(nodes, nodes[1:], strict=False))
                if any(edge not in road_miles for edge in edges):
                    continue
                miles = [road_miles[edge] for edge in edges]
                cost = independent_path_cost(
                    tuple(prices[stop - 1] for stop in stops),
                    miles,
                    initial,
                )
                if cost is not None:
                    best = cost if best is None else min(best, cost)
        result = search_fuel_graph(graph, Decimal(initial))
        assert result.complete
        if best is None:
            assert result.solution is None
        else:
            assert result.solution is not None
            assert abs(float(result.solution.purchase_cost) - best) < 1e-6
            seeded = search_fuel_graph(
                graph,
                Decimal(initial),
                seed_station_ids=result.solution.station_ids,
            )
            assert seeded.complete and seeded.solution is not None
            assert abs(float(seeded.solution.purchase_cost) - best) < 1e-6


def test_random_time_limited_graphs_match_enumerated_lp_oracle() -> None:
    rng = random.Random(17031)
    for _ in range(30):
        count = 3
        prices = tuple(rng.randint(1, 7) for _ in range(count))
        initial = rng.randint(10, 50)
        time_limit = rng.randint(100, 550)
        size = count + 2
        distances: list[list[Decimal | None]] = [[None] * size for _ in range(size)]
        durations: list[list[Decimal | None]] = [[None] * size for _ in range(size)]
        edges: dict[tuple[int, int], tuple[int, int]] = {}
        for source in range(size - 1):
            for target in range(1, size):
                if source == target or rng.random() < 0.2:
                    continue
                miles = rng.randint(30, 450)
                minutes = rng.randint(30, 300)
                edges[source, target] = miles, minutes
                distances[source][target] = Decimal(miles) * METERS_PER_MILE
                durations[source][target] = Decimal(minutes * 60)
        graph = DirectedFuelGraph(
            tuple(
                FuelOpportunity(str(index), Decimal(price))
                for index, price in enumerate(prices)
            ),
            tuple(tuple(row) for row in distances),
            "oracle-time-random",
            tuple(tuple(row) for row in durations),
        )
        best = None
        for stop_count in range(count + 1):
            for stops in itertools.permutations(range(1, count + 1), stop_count):
                nodes = (0, *stops, size - 1)
                path_edges = list(zip(nodes, nodes[1:], strict=False))
                if any(edge not in edges for edge in path_edges):
                    continue
                if sum(edges[edge][1] for edge in path_edges) > time_limit:
                    continue
                miles = [edges[edge][0] for edge in path_edges]
                cost = independent_path_cost(
                    tuple(prices[stop - 1] for stop in stops), miles, initial
                )
                if cost is not None:
                    best = cost if best is None else min(best, cost)
        result = search_fuel_graph(
            graph,
            Decimal(initial),
            max_total_seconds=Decimal(time_limit * 60),
        )
        assert result.complete
        if best is None:
            assert result.solution is None
        else:
            assert result.solution is not None
            assert abs(float(result.solution.purchase_cost) - best) < 1e-6
            seeded = search_fuel_graph(
                graph,
                Decimal(initial),
                max_total_seconds=Decimal(time_limit * 60),
                seed_station_ids=result.solution.station_ids,
            )
            assert seeded.complete and seeded.solution is not None
            assert abs(float(seeded.solution.purchase_cost) - best) < 1e-6


def test_default_thirty_minute_bound_chooses_cheapest_feasible_station() -> None:
    mile = METERS_PER_MILE
    graph = DirectedFuelGraph(
        (FuelOpportunity("cheap", Decimal(1)), FuelOpportunity("near", Decimal(2))),
        (
            (Decimal(0), 50 * mile, 50 * mile, 100 * mile),
            (None, Decimal(0), None, 50 * mile),
            (None, None, Decimal(0), 50 * mile),
            (None, None, None, Decimal(0)),
        ),
        "thirty-minute-oracle",
        (
            (Decimal(0), Decimal(75 * 60), Decimal(75 * 60), Decimal(120 * 60)),
            (None, Decimal(0), None, Decimal(76 * 60)),
            (None, None, Decimal(0), Decimal(75 * 60)),
            (None, None, None, Decimal(0)),
        ),
    )
    result = search_fuel_graph(
        graph,
        Decimal(5),
        max_total_seconds=Decimal((120 + 30) * 60),
    )
    assert result.complete
    assert result.solution is not None
    assert result.solution.station_ids == ("near",)
    independent_cost = independent_path_cost((2,), [50, 50], 5)
    assert independent_cost == 10
    assert float(result.solution.purchase_cost) == independent_cost
