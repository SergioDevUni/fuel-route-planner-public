"""Joint fuel, cost, time, distance, and visited-set correctness against LP."""

import itertools
import random
from decimal import Decimal

from app.domain.fuel import FuelOpportunity, VehicleSpec
from app.domain.units import METERS_PER_MILE
from app.planning.fuel_graph import DirectedFuelGraph, SparseDirectedFuelGraph
from app.planning.graph_search import search_fuel_graph
from tests.support.oracle import independent_path_cost


def test_varied_resource_labels_match_all_simple_paths_and_lp() -> None:
    rng = random.Random(570036)
    for _ in range(100):
        count, size = 4, 6
        capacity = rng.randint(10, 50)
        reserve = rng.randint(0, capacity - 1)
        initial = rng.randint(reserve, capacity)
        prices = tuple(rng.randint(1, 7) for _ in range(count))
        time_limit, distance_limit = rng.randint(50, 500), rng.randint(100, 1300)
        edges = {
            (a, b): (rng.randint(0, 500), rng.randint(0, 300))
            for a in range(size - 1)
            for b in range(1, size)
            if a != b and rng.random() > 0.2
        }
        stations = tuple(
            FuelOpportunity(str(i), Decimal(p)) for i, p in enumerate(prices)
        )
        distance = tuple(
            tuple(
                Decimal(edges[a, b][0]) * METERS_PER_MILE if (a, b) in edges else None
                for b in range(size)
            )
            for a in range(size)
        )
        duration = tuple(
            tuple(
                Decimal(edges[a, b][1]) if (a, b) in edges else None
                for b in range(size)
            )
            for a in range(size)
        )
        dense = DirectedFuelGraph(stations, distance, "resource-oracle", duration)
        sparse = SparseDirectedFuelGraph(
            stations,
            tuple(
                tuple(
                    (b, Decimal(m) * METERS_PER_MILE, Decimal(t))
                    for (a, b), (m, t) in edges.items()
                    if a == source
                )
                for source in range(size)
            ),
            "sparse-resource-oracle",
        )
        best = None
        for length in range(count + 1):
            for path in itertools.permutations(range(1, count + 1), length):
                nodes = (0, *path, size - 1)
                legs = list(zip(nodes, nodes[1:], strict=False))
                if any(edge not in edges for edge in legs):
                    continue
                miles = [edges[e][0] for e in legs]
                if (
                    sum(miles) > distance_limit
                    or sum(edges[e][1] for e in legs) > time_limit
                ):
                    continue
                cost = independent_path_cost(
                    tuple(prices[node - 1] for node in path),
                    miles,
                    initial,
                    reserve,
                    capacity,
                )
                if cost is not None:
                    key = (
                        round(cost, 7),
                        sum(miles),
                        len(path),
                        tuple(str(node - 1) for node in path),
                    )
                    best = key if best is None else min(best, key)
        for graph in (dense, sparse):
            result = search_fuel_graph(
                graph,
                Decimal(initial),
                Decimal(reserve),
                vehicle=VehicleSpec(Decimal(capacity)),
                max_total_seconds=Decimal(time_limit),
                max_total_meters=Decimal(distance_limit) * METERS_PER_MILE,
            )
            assert result.complete
            if best is None:
                assert result.solution is None
            else:
                assert result.solution is not None
                actual = result.solution
                assert abs(float(actual.purchase_cost) - best[0]) < 1e-6
                assert (
                    actual.total_distance_meters == Decimal(best[1]) * METERS_PER_MILE
                )
                assert (len(actual.station_ids), actual.station_ids) == best[2:]
