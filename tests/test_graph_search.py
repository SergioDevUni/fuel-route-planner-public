"""Directed fuel search checks with small, fully specified road graphs."""

from decimal import Decimal

import pytest

from app.planning.fuel_graph import DirectedFuelGraph
from app.planning.graph_search import search_fuel_graph
from tests.support.graph import graph, miles


def test_longer_cheaper_route_wins() -> None:
    roads = graph(
        (("expensive", "5"), ("cheap", "1")),
        {
            (0, 1): 250,
            (1, 3): 250,
            (0, 2): 300,
            (2, 3): 300,
            (1, 2): 150,
            (2, 1): 150,
        },
    )
    result = search_fuel_graph(roads, Decimal(30))
    assert result.complete
    assert result.solution is not None
    assert result.solution.station_ids == ("cheap",)
    assert result.solution.purchase_cost == Decimal(30)
    assert result.solution.total_distance_meters == miles(600)


def test_verified_graph_seed_survives_an_immediate_search_cutoff() -> None:
    roads = graph(
        (("expensive", "5"), ("cheap", "1")),
        {(0, 1): 250, (1, 3): 250, (0, 2): 300, (2, 3): 300},
    )
    result = search_fuel_graph(
        roads, Decimal(30), seed_station_ids=("cheap",), stop_when=lambda: True
    )
    assert not result.complete
    assert result.solution is not None
    assert result.solution.station_ids == ("cheap",)
    assert result.solution.purchase_cost == Decimal(30)


def test_expensive_extension_cannot_consume_the_rest_of_the_search_batch(
    monkeypatch,
) -> None:
    from app.planning.fuel_profile import FuelCostProfile

    roads = graph(
        (("expensive", "5"), ("cheap", "1")),
        {(0, 1): 250, (1, 3): 250, (0, 2): 300, (2, 3): 300},
    )
    work = [0]
    original = FuelCostProfile.buy

    def expensive_buy(self, *args):
        work[0] += 1
        return original(self, *args)

    monkeypatch.setattr(FuelCostProfile, "buy", expensive_buy)
    result = search_fuel_graph(roads, Decimal(30), stop_when=lambda: work[0] >= 1)
    assert not result.complete
    assert result.expansions == 1
    assert work[0] == 1
    assert result.solution is not None
    assert result.solution.station_ids == ("expensive",)


def test_seed_cannot_bypass_real_fuel_limit() -> None:
    roads = graph(
        (("unreachable-cheap", "1"), ("near", "5")),
        {(0, 1): 501, (1, 3): 1, (0, 2): 250, (2, 3): 250},
    )
    result = search_fuel_graph(
        roads,
        Decimal(30),
        seed_station_ids=("unreachable-cheap",),
        stop_when=lambda: True,
    )
    assert result.solution is not None
    assert result.solution.station_ids == ("near",)


def test_time_cap_excludes_cheaper_but_much_slower_route() -> None:
    roads = graph(
        (("near-expensive", "5"), ("far-cheap", "1")),
        {(0, 1): 250, (1, 3): 250, (0, 2): 300, (2, 3): 300},
    )
    timed = DirectedFuelGraph(
        roads.stations,
        roads.distances_meters,
        roads.version,
        (
            (None, Decimal(400), Decimal(1300), None),
            (None, None, None, Decimal(400)),
            (None, None, None, Decimal(1300)),
            (None, None, None, None),
        ),
    )
    unrestricted = search_fuel_graph(timed, Decimal(30))
    assert unrestricted.solution is not None
    assert unrestricted.solution.station_ids == ("far-cheap",)
    balanced = search_fuel_graph(timed, Decimal(30), max_total_seconds=Decimal(1800))
    assert balanced.complete
    assert balanced.solution is not None
    assert balanced.solution.station_ids == ("near-expensive",)
    invalid_time_seed = search_fuel_graph(
        timed,
        Decimal(30),
        max_total_seconds=Decimal(1800),
        seed_station_ids=("far-cheap",),
        stop_when=lambda: True,
    )
    assert invalid_time_seed.solution is not None
    assert invalid_time_seed.solution.station_ids == ("near-expensive",)
    with pytest.raises(ValueError, match="duration"):
        search_fuel_graph(roads, Decimal(30), max_total_seconds=Decimal(1800))


def test_sparse_bridge_and_first_leg_fuel_limit() -> None:
    roads = graph(
        (("a", "4"), ("b", "2")),
        {(0, 1): 400, (1, 2): 400, (2, 3): 400},
    )
    result = search_fuel_graph(roads, Decimal(40))
    assert result.complete
    assert result.solution is not None
    assert result.solution.station_ids == ("a", "b")
    assert result.solution.purchase_cost == Decimal(240)
    assert search_fuel_graph(roads, Decimal(39)).solution is None


def test_directed_unreachable_and_expansion_limit() -> None:
    roads = graph(
        (("a", "2"),),
        {(0, 1): 400, (1, 2): 400, (2, 1): 100},
    )
    assert search_fuel_graph(roads, Decimal(40), max_expansions=1).complete is False
    complete = search_fuel_graph(roads, Decimal(40))
    assert complete.complete and complete.solution is not None
    reverse_only = graph((("a", "2"),), {(1, 0): 400, (2, 1): 400})
    assert search_fuel_graph(reverse_only, Decimal(40)).solution is None


def test_early_stop_keeps_feasible_fastest_chain_as_incomplete_incumbent() -> None:
    roads = graph(
        (("bridge", "3"),),
        {(0, 1): 400, (1, 2): 400},
    )
    result = search_fuel_graph(roads, Decimal(40), stop_when=lambda: True)
    assert not result.complete
    assert result.expansions == 0
    assert result.solution is not None
    assert result.solution.station_ids == ("bridge",)


def test_generic_insertion_improves_incumbent_before_expansion_cap() -> None:
    roads = graph(
        (("expensive", "5"), ("cheap", "1")),
        {(0, 1): 10, (1, 3): 499, (0, 2): 11, (2, 1): 10},
    )
    result = search_fuel_graph(roads, Decimal(10), max_expansions=1)
    assert not result.complete
    assert result.solution is not None
    assert result.solution.station_ids == ("cheap", "expensive")
    assert result.solution.purchase_cost == Decimal("45.6")
    assert result.solution.fuel.visits[0].departure_gallons == Decimal(50)


def test_equal_price_tie_and_exact_range_edge() -> None:
    roads = graph(
        (("a", "2"), ("b", "2")),
        {(0, 1): 300, (1, 3): 300, (0, 2): 300, (2, 3): 300},
    )
    result = search_fuel_graph(roads, Decimal(30))
    assert result.complete
    assert result.solution is not None
    assert result.solution.station_ids == ("a",)
    direct = graph((), {(0, 1): 500})
    assert search_fuel_graph(direct, Decimal(50)).solution is not None
    too_far = graph((), {(0, 1): 501})
    assert search_fuel_graph(too_far, Decimal(50)).solution is None


def test_zero_purchase_waypoint_is_not_returned_as_a_fuel_stop() -> None:
    shared = graph(
        (("a-expensive", "5"), ("z-cheap", "1")),
        {
            (0, 1): 300,
            (0, 2): 300,
            (1, 2): 0,
            (1, 3): 300,
            (2, 3): 300,
        },
    )
    result = search_fuel_graph(shared, Decimal(30))
    assert result.solution is not None
    assert result.solution.station_ids == ("z-cheap",)
