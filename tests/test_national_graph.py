"""Cached road points keep each CSV offer and reject incomplete road data."""

import sqlite3
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.planning.candidates import StationCandidate
from app.planning.endpoint_block_cache import EndpointBlockCache
from app.planning.fuel_graph import SparseDirectedFuelGraph
from app.planning.national_graph import (
    StationRoadGraphUnavailable,
    _endpoint_batches,
    build_trip_road_graph,
    completed_road_points,
)
from app.routing.local_valhalla import LocalRoadBlock
from scripts.road_precompute.storage import _connect, _initialize


class LocalStub:
    graph_version = "test-road"
    max_points = 20

    def __init__(self) -> None:
        self.matrix_calls: list[
            tuple[tuple[Coordinates, ...], tuple[Coordinates, ...]]
        ] = []

    def locate(
        self, points: tuple[Coordinates, ...], budget: RequestBudget
    ) -> tuple[Decimal, ...]:
        return tuple(Decimal(0) for _ in points)

    def matrix_block(
        self,
        sources: tuple[Coordinates, ...],
        targets: tuple[Coordinates, ...],
        budget: RequestBudget,
    ) -> LocalRoadBlock:
        self.matrix_calls.append((sources, targets))
        meters = tuple(tuple(Decimal(10000) for _ in targets) for _ in sources)
        seconds = tuple(tuple(Decimal(600) for _ in targets) for _ in sources)
        return LocalRoadBlock(meters, seconds)


def _station(station_id: str, price: str, point: Coordinates) -> StationCandidate:
    return StationCandidate(
        station_id, station_id, "Road 1", "Test", "IL", point, Decimal(price)
    )


def _cache(path: Path, point: Coordinates, *, complete: bool, snap: int = 12) -> None:
    connection = _connect(path)
    try:
        _initialize(connection, (point,), "source", "candidate", "test-road")
        with connection:
            connection.execute(
                "UPDATE point SET snap_meters=? WHERE point_id=0", (snap,)
            )
            if complete:
                connection.execute("INSERT INTO complete_source VALUES (0)")
    finally:
        connection.close()


def test_co_located_csv_offers_remain_separate(tmp_path: Path) -> None:
    point = Coordinates(41.0, -89.0)
    database = tmp_path / "edges.db"
    _cache(database, point, complete=True)
    local = LocalStub()
    result = build_trip_road_graph(
        database,
        (_station("a", "3", point), _station("b", "2", point)),
        Coordinates(41.01, -89.01),
        Coordinates(40.99, -88.99),
        local,  # type: ignore[arg-type]
        RequestBudget(),
        candidate_version="candidate",
        initial_gallons=Decimal(20),
        reserve_gallons=Decimal(1),
    )
    assert tuple(item.station_id for item in result.graph.stations) == ("a", "b")
    assert result.graph.distances_meters[1][2] == 0
    assert result.graph.distances_meters[2][1] == 0
    assert result.graph.distances_meters[0][1] == 10000
    assert result.graph.distances_meters[2][3] == 10000
    assert result.unavailable_ids == ()
    assert set(local.matrix_calls) == {
        ((Coordinates(41.01, -89.01),), (point,)),
        ((point,), (Coordinates(40.99, -88.99),)),
        ((Coordinates(41.01, -89.01),), (Coordinates(40.99, -88.99),)),
    }


def test_sparse_graph_keeps_co_located_offers_and_endpoint_edges(
    tmp_path: Path,
) -> None:
    point = Coordinates(41.0, -89.0)
    database = tmp_path / "edges.db"
    _cache(database, point, complete=True)
    result = build_trip_road_graph(
        database,
        (_station("a", "3", point), _station("b", "2", point)),
        Coordinates(41.01, -89.01),
        Coordinates(40.99, -88.99),
        LocalStub(),  # type: ignore[arg-type]
        RequestBudget(),
        candidate_version="candidate",
        initial_gallons=Decimal(20),
        reserve_gallons=Decimal(1),
        sparse=True,
    )
    assert isinstance(result.graph, SparseDirectedFuelGraph)
    assert result.graph.outgoing[0] == (
        (1, Decimal(10000), Decimal(600)),
        (2, Decimal(10000), Decimal(600)),
        (3, Decimal(10000), Decimal(600)),
    )
    assert result.graph.outgoing[1][0] == (2, Decimal(0), Decimal(0))
    assert result.graph.outgoing[2][0] == (1, Decimal(0), Decimal(0))


def test_endpoint_batches_keep_nearby_points_together() -> None:
    points = {
        0: Coordinates(39.0, -104.5),
        1: Coordinates(35.0, -106.0),
        2: Coordinates(39.1, -104.6),
        3: Coordinates(35.1, -106.1),
    }
    assert _endpoint_batches((0, 1, 2, 3), points, 2) == ((1, 3), (0, 2))


def test_endpoint_screen_keeps_road_reachable_snap_margin(tmp_path):
    point = Coordinates(41, -89)
    database = tmp_path / "edges.db"
    _cache(database, point, complete=True)
    local = LocalStub()
    # One gallon reaches 16,093.44 m. Requested points are 16,500 m apart,
    # while the measured snapped road leg is only 10,000 m.
    with patch(
        "app.planning.national_graph.geodesic_meters", return_value=Decimal(16500)
    ):
        result = build_trip_road_graph(
            database,
            (_station("snap-access", "3", point),),
            Coordinates(41.01, -89.01),
            Coordinates(40.99, -88.99),
            local,
            RequestBudget(),
            candidate_version="candidate",
            initial_gallons=Decimal(1),
            reserve_gallons=Decimal(0),
        )
    assert result.graph.distances_meters[0][1] == 10000
    assert result.graph.distances_meters[0][2] == 10000


def test_incomplete_cache_does_not_silently_drop_offer(tmp_path: Path) -> None:
    point = Coordinates(41.0, -89.0)
    database = tmp_path / "edges.db"
    _cache(database, point, complete=False)
    with pytest.raises(StationRoadGraphUnavailable, match="incomplete"):
        build_trip_road_graph(
            database,
            (_station("a", "3", point),),
            Coordinates(41.01, -89.01),
            Coordinates(40.99, -88.99),
            LocalStub(),  # type: ignore[arg-type]
            RequestBudget(),
            candidate_version="candidate",
            initial_gallons=Decimal(20),
            reserve_gallons=Decimal(1),
        )


def test_completed_points_reader_requires_matching_version(tmp_path: Path) -> None:
    point = Coordinates(41.0, -89.0)
    database = tmp_path / "edges.db"
    _cache(database, point, complete=False)
    assert completed_road_points(database, "candidate", "test-road") == set()
    with pytest.raises(StationRoadGraphUnavailable, match="version"):
        completed_road_points(database, "other", "test-road")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO complete_source VALUES (0)")
    assert completed_road_points(database, "candidate", "test-road") == {point}


def test_road_graph_uses_selected_stop_snap_policy(tmp_path: Path) -> None:
    point = Coordinates(41.0, -89.0)
    database = tmp_path / "edges.db"
    _cache(database, point, complete=True, snap=300)
    site = _station("site", "3", point)
    locality = StationCandidate(
        "locality",
        "locality",
        "Road 1",
        "Test",
        "IL",
        point,
        Decimal(2),
        "city_representative",
    )
    result = build_trip_road_graph(
        database,
        (site, locality),
        Coordinates(41.01, -89.01),
        Coordinates(40.99, -88.99),
        LocalStub(),  # type: ignore[arg-type]
        RequestBudget(),
        candidate_version="candidate",
        initial_gallons=Decimal(20),
        reserve_gallons=Decimal(1),
    )
    assert tuple(station.station_id for station in result.retained) == ("locality",)
    assert result.unavailable_ids == ("site",)


def test_endpoint_blocks_are_reused_on_identical_trip(tmp_path: Path) -> None:
    point = Coordinates(41.0, -89.0)
    database = tmp_path / "edges.db"
    _cache(database, point, complete=True)
    local = LocalStub()
    endpoint_cache = EndpointBlockCache(tmp_path / "endpoints", local)  # type: ignore[arg-type]
    options = dict(
        candidate_version="candidate",
        initial_gallons=Decimal(20),
        reserve_gallons=Decimal(1),
        endpoint_cache=endpoint_cache,
    )
    first = build_trip_road_graph(
        database,
        (_station("a", "3", point),),
        Coordinates(41.01, -89.01),
        Coordinates(40.99, -88.99),
        local,  # type: ignore[arg-type]
        RequestBudget(),
        **options,  # type: ignore[arg-type]
    )
    assert len(local.matrix_calls) == 3
    second = build_trip_road_graph(
        database,
        (_station("a", "3", point),),
        Coordinates(41.01, -89.01),
        Coordinates(40.99, -88.99),
        local,  # type: ignore[arg-type]
        RequestBudget(),
        **options,  # type: ignore[arg-type]
    )
    assert second.graph == first.graph
    assert len(local.matrix_calls) == 3
