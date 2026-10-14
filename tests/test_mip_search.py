"""The integer solver agrees with exhaustive search on small road graphs."""

from decimal import Decimal

from app.planning.fuel_graph import DirectedFuelGraph
from app.planning.graph_search import search_fuel_graph
from app.planning.mip_search import search_fuel_graph_mip
from tests.support.graph import graph, miles


def test_cheaper_detour_and_time_limit() -> None:
    roads = graph(
        (("expensive", "5"), ("cheap", "1")),
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
    for cap in (None, Decimal(1800)):
        expected = search_fuel_graph(timed, Decimal(30), max_total_seconds=cap)
        actual = search_fuel_graph_mip(timed, Decimal(30), max_total_seconds=cap)
        assert actual.complete
        assert actual.solution is not None
        assert expected.solution is not None
        assert actual.solution.station_ids == expected.solution.station_ids
        assert actual.solution.purchase_cost == expected.solution.purchase_cost


def test_multi_stop_and_infeasible() -> None:
    roads = graph(
        (("a", "4"), ("b", "2")),
        {(0, 1): 400, (1, 2): 400, (2, 3): 400},
    )
    actual = search_fuel_graph_mip(roads, Decimal(40))
    assert actual.complete
    assert actual.solution is not None
    assert actual.solution.station_ids == ("a", "b")
    assert actual.solution.purchase_cost == Decimal(240)
    assert search_fuel_graph_mip(roads, Decimal(39)).solution is None


def test_direct_and_distance_limit() -> None:
    roads = graph(
        (("a", "1"),),
        {(0, 1): 200, (1, 2): 200, (0, 2): 300},
    )
    direct = search_fuel_graph_mip(roads, Decimal(40), max_total_meters=miles(300))
    assert direct.complete
    assert direct.solution is not None
    assert direct.solution.station_ids == ()
    assert direct.solution.purchase_cost == 0
