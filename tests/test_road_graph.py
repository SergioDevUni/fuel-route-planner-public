"""Road-matrix integration keeps CSV offers separate and rejects bad snaps."""

from decimal import Decimal

import pytest

from app.domain.road import RoadDistanceMatrix
from app.domain.routing import Coordinates
from app.domain.units import METERS_PER_MILE
from app.planning.candidates import StationCandidate
from app.planning.graph_search import search_fuel_graph
from app.planning.road_graph import graph_from_matrix


def miles(value: int) -> Decimal:
    return Decimal(value) * METERS_PER_MILE


def station(station_id: str, price: str) -> StationCandidate:
    return StationCandidate(
        station_id=station_id,
        name=station_id,
        address="shared exit",
        city="Example",
        state="TX",
        coordinates=Coordinates(33, -101),
        price_per_gallon=Decimal(price),
        location_precision="interstate_exit",
    )


def matrix(*, cheap_snap: Decimal = Decimal(0)) -> RoadDistanceMatrix:
    return RoadDistanceMatrix(
        distances_meters=(
            (Decimal(0), miles(300), miles(300), miles(650)),
            (None, Decimal(0), Decimal(0), miles(300)),
            (None, Decimal(0), Decimal(0), miles(300)),
            (None, None, None, Decimal(0)),
        ),
        source_snap_meters=(Decimal(0), Decimal(0), cheap_snap, Decimal(0)),
        destination_snap_meters=(Decimal(0), Decimal(0), cheap_snap, Decimal(0)),
    )


def test_shared_coordinate_keeps_distinct_prices_for_search() -> None:
    graph = graph_from_matrix(
        (station("expensive", "5"), station("cheap", "1")),
        matrix(),
        "matrix-v1",
        station_point_indices=(1, 2),
    )
    result = search_fuel_graph(graph, Decimal(30))
    assert result.complete and result.solution is not None
    assert result.solution.station_ids == ("cheap",)
    assert result.solution.purchase_cost == Decimal(30)


def test_bad_station_snap_is_excluded_without_losing_other_offer() -> None:
    graph = graph_from_matrix(
        (station("expensive", "5"), station("cheap", "1")),
        matrix(cheap_snap=Decimal(251)),
        "matrix-v1",
        station_point_indices=(1, 2),
    )
    assert tuple(item.station_id for item in graph.stations) == ("expensive",)
    result = search_fuel_graph(graph, Decimal(30))
    assert result.solution is not None
    assert result.solution.purchase_cost == Decimal(150)


def test_bad_endpoint_snap_invalidates_graph() -> None:
    source = matrix()
    bad = RoadDistanceMatrix(
        source.distances_meters,
        (Decimal(1001), *source.source_snap_meters[1:]),
        source.destination_snap_meters,
    )
    with pytest.raises(ValueError, match="endpoint snap"):
        graph_from_matrix(
            (station("expensive", "5"), station("cheap", "1")),
            bad,
            "matrix-v1",
            station_point_indices=(1, 2),
        )


def test_shared_point_uses_one_matrix_location_without_merging_offers() -> None:
    compact = RoadDistanceMatrix(
        (
            (Decimal(0), miles(300), miles(650)),
            (None, Decimal(0), miles(300)),
            (None, None, Decimal(0)),
        ),
        (Decimal(0),) * 3,
        (Decimal(0),) * 3,
    )
    graph = graph_from_matrix(
        (station("expensive", "5"), station("cheap", "1")),
        compact,
        "compact-v1",
        station_point_indices=(1, 1),
    )
    assert len(graph.distances_meters) == 4
    assert graph.distances_meters[1][2] == 0
    result = search_fuel_graph(graph, Decimal(30))
    assert result.solution is not None
    assert result.solution.station_ids == ("cheap",)


def test_endpoint_aliases_support_starting_refuel_and_distinct_csv_prices() -> None:
    compact = RoadDistanceMatrix(
        ((Decimal(0), miles(100)), (None, Decimal(0))),
        (Decimal(0),) * 2,
        (Decimal(0),) * 2,
    )
    graph = graph_from_matrix(
        (
            station("origin-expensive", "5"),
            station("origin-cheap", "1"),
            station("finish", "2"),
        ),
        compact,
        "endpoint-alias",
        station_point_indices=(0, 0, 1),
    )
    result = search_fuel_graph(graph, Decimal(2), Decimal(2))
    assert result.complete and result.solution is not None
    assert result.solution.station_ids == ("origin-cheap",)
    assert result.solution.purchase_cost == Decimal(10)
    assert result.solution.fuel.ending_gallons == Decimal(2)


def test_endpoint_offer_still_obeys_station_snap_limit() -> None:
    compact = RoadDistanceMatrix(
        ((Decimal(0), miles(100)), (None, Decimal(0))),
        (Decimal(300), Decimal(0)),
        (Decimal(300), Decimal(0)),
    )
    graph = graph_from_matrix(
        (station("origin", "1"),), compact, "endpoint-snap", station_point_indices=(0,)
    )
    assert not graph.stations
    assert search_fuel_graph(graph, Decimal(2)).solution is None


@pytest.mark.parametrize("index", [-1, 2])
def test_out_of_matrix_offer_index_is_rejected(index: int) -> None:
    compact = RoadDistanceMatrix(
        ((Decimal(0), miles(100)), (None, Decimal(0))),
        (Decimal(0),) * 2,
        (Decimal(0),) * 2,
    )
    with pytest.raises(ValueError, match="point indices"):
        graph_from_matrix(
            (station("invalid", "1"),),
            compact,
            "invalid-index",
            station_point_indices=(index,),
        )
