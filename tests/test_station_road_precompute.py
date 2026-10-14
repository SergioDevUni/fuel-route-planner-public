"""Offline station-edge build preserves range and version boundaries."""

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain.routing import Coordinates
from app.routing.local_valhalla import (
    LocalRoadBlock,
    LocalValhallaNoPath,
    LocalValhallaUnavailable,
)
from scripts.road_precompute.acquisition import (
    MatrixCoverageGap,
    _checked_block_edges,
    _retry_local_road_call,
)
from scripts.road_precompute.builder import build
from scripts.road_precompute.scheduling import (
    _priority_groups,
    _read_priority_file,
    _source_targets,
    _spatial_source_groups,
)
from scripts.road_precompute.storage import _connect, _initialize


def test_geodesic_range_screen_keeps_cross_state_leg() -> None:
    points = (
        Coordinates(41.8781, -87.6298),  # Chicago
        Coordinates(38.6270, -90.1994),  # St. Louis
        Coordinates(34.0522, -118.2437),  # Los Angeles
    )
    by_source, targets = _source_targets([0], [0, 1, 2], points)
    assert by_source[0] == {0, 1}
    assert targets == [0, 1]


def test_target_screen_relaxes_both_allowed_road_snaps(monkeypatch):
    from scripts.road_precompute.policy import MAX_LEG_METERS

    monkeypatch.setattr(
        "scripts.road_precompute.scheduling.GEOD",
        SimpleNamespace(inv=lambda *args: (0, 0, MAX_LEG_METERS + 1000)),
    )
    points = (Coordinates(40, -90), Coordinates(40, -80))
    by_source, targets = _source_targets([0], [1], points)
    assert by_source == {0: {1}}
    assert targets == [1]


def test_source_groups_keep_rectangular_queries_local() -> None:
    points = (
        Coordinates(41.8781, -87.6298),
        Coordinates(41.88, -87.64),
        Coordinates(38.627, -90.1994),
    )
    assert _spatial_source_groups([0, 1, 2], points, 20) == [[2], [0, 1]]


def test_target_groups_separate_opposite_sides_of_range_circle() -> None:
    points = (
        Coordinates(39, -105),
        Coordinates(39, -113),
        Coordinates(39, -97),
    )
    assert _spatial_source_groups([1, 2], points, 20, cell_degrees=5.0) == [[1], [2]]


def test_priority_sources_run_before_default_spatial_order(tmp_path) -> None:
    points = (
        Coordinates(25, -80),
        Coordinates(35, -105),
        Coordinates(40, -105),
    )
    priority = tmp_path / "priority.json"
    priority.write_text(
        json.dumps(
            {
                "candidate_version": "csv-v1",
                "graph_version": "road-v1",
                "point_ids": [2, 1],
            }
        )
    )
    ids = _read_priority_file(priority, "csv-v1", "road-v1", len(points))
    groups, count = _priority_groups([0, 1, 2], points, 20, ids)
    assert groups[:count] == [[1], [2]]
    assert groups[count:] == [[0]]
    with pytest.raises(ValueError, match="version"):
        _read_priority_file(priority, "other", "road-v1", len(points))


def test_resume_rejects_other_csv_or_road_graph(tmp_path) -> None:
    connection = _connect(tmp_path / "edges.sqlite")
    points = (Coordinates(40, -90), Coordinates(40.1, -90.1))
    try:
        _initialize(connection, points, "csv-a", "points-a", "road-a")
        _initialize(connection, points, "csv-a", "points-a", "road-a")
        with pytest.raises(ValueError, match="another CSV or road version"):
            _initialize(connection, points, "csv-a", "points-a", "road-b")
        assert connection.execute("SELECT COUNT(*) FROM point").fetchone() == (2,)
    finally:
        connection.close()


def test_blank_fast_matrix_leg_cannot_complete_a_station_source() -> None:
    block = LocalRoadBlock(((None, Decimal(900_000)),), ((None, Decimal(100)),))
    with pytest.raises(MatrixCoverageGap, match="unresolved"):
        _checked_block_edges([0], [1, 2], {0: {1, 2}}, block)
    complete = LocalRoadBlock(
        ((Decimal(700_000), Decimal(900_000)),),
        ((Decimal(20_000), Decimal(30_000)),),
    )
    assert _checked_block_edges([0], [1, 2], {0: {1, 2}}, complete) == [
        (0, 1, 700_000.0, 20_000.0)
    ]


def test_blank_matrix_leg_is_repaired_by_a_complete_route() -> None:
    block = LocalRoadBlock(((None,),), ((None,),))
    assert _checked_block_edges(
        [0],
        [1],
        {0: {1}},
        block,
        lambda source, target: (Decimal(595_922), Decimal(20_000)),
    ) == [(0, 1, 595_922.0, 20_000.0)]


def test_parallel_repairs_keep_each_target_and_its_road_cost() -> None:
    block = LocalRoadBlock(((None, None),), ((None, None),))
    with ThreadPoolExecutor(max_workers=2) as executor:
        edges = _checked_block_edges(
            [0],
            [1, 2],
            {0: {1, 2}},
            block,
            lambda source, target: (
                Decimal(500_000 + target),
                Decimal(10_000 + target),
            ),
            executor,
        )
    assert edges == [
        (0, 1, 500_001.0, 10_001.0),
        (0, 2, 500_002.0, 10_002.0),
    ]


def test_offline_worker_count_is_bounded(tmp_path) -> None:
    with pytest.raises(ValueError, match="worker count"):
        build(
            tmp_path,
            tmp_path / "edges.sqlite",
            "http://127.0.0.1:8004",
            "graph-v1",
            batch_size=20,
            workers=13,
            max_groups=1,
        )
    with pytest.raises(ValueError, match="priority file"):
        build(
            tmp_path,
            tmp_path / "edges.sqlite",
            "http://127.0.0.1:8004",
            "graph-v1",
            batch_size=20,
            workers=4,
            max_groups=1,
            priority_only=True,
        )


def test_offline_road_call_retries_timeout_but_not_no_path() -> None:
    calls = 0
    waits: list[float] = []

    def transient() -> int:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise LocalValhallaUnavailable("timed out")
        return 17

    assert _retry_local_road_call(transient, sleep=waits.append) == 17
    assert calls == 3
    assert waits == [1, 2]

    def no_path() -> int:
        raise LocalValhallaNoPath("unreachable")

    waits.clear()
    with pytest.raises(LocalValhallaNoPath):
        _retry_local_road_call(no_path, sleep=waits.append)
    assert waits == []
