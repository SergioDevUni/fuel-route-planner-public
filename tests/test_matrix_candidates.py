"""Matrix candidate coverage and shared-point accounting."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.routing import Coordinates
from app.domain.units import METERS_PER_MILE
from app.planning.candidates import ProjectedCandidate, StationCandidate
from app.planning.matrix_candidates import matrix_points, select_matrix_candidates


def candidate(
    station_id: str, progress: int, price: str, longitude: float
) -> ProjectedCandidate:
    station = StationCandidate(
        station_id,
        station_id,
        "Exit",
        "Example",
        "TX",
        Coordinates(32, longitude),
        Decimal(price),
    )
    return ProjectedCandidate(
        station,
        Decimal(progress),
        Decimal(100),
        f"{station_id}:1",
    )


def test_first_stop_access_and_price_survive_projected_early_remote_cluster():
    access = candidate("access", 90_000, "7", -100.001)
    cheap = candidate("cheap", 90_000, "1", -100.01)
    remote = candidate("remote", 1, "0.5", -110)
    chosen = select_matrix_candidates(
        (),
        (access, cheap, remote),
        Decimal(100_000),
        start=Coordinates(32, -100),
        initial_gallons=Decimal(1),
        max_locations=4,
    )
    assert chosen is not None
    assert {v.station.station_id for v in chosen} == {"access", "cheap"}


def test_cheap_early_cluster_cannot_displace_late_bridge() -> None:
    early = tuple(
        candidate(f"early-{index}", 10 + index, "1", -100 - index / 100)
        for index in range(30)
    )
    bridge = candidate("late-bridge", 900, "5", -102)
    chosen = select_matrix_candidates((), (*early, bridge), Decimal(1000))
    assert chosen is not None
    assert "late-bridge" in {item.station.station_id for item in chosen}
    assert len({item.station.coordinates for item in chosen}) <= 57


def test_matrix_points_share_location_without_merging_csv_offers() -> None:
    first = candidate("first", 300, "4", -101)
    second = candidate("second", 301, "3", -101)
    third = candidate("third", 600, "2", -102)
    points, offer_indices = matrix_points(
        Coordinates(32, -100),
        (first, second, third),
        Coordinates(32, -103),
    )
    assert len(points) == 4
    assert offer_indices == (1, 1, 2)


def test_endpoint_offers_share_provider_points_and_keep_distinct_nodes() -> None:
    start, finish = Coordinates(32, -100), Coordinates(32, -103)
    offers = (
        candidate("start", 0, "3", -100),
        candidate("finish", 1000, "2", -103),
        candidate("interior", 500, "4", -101),
        candidate("start-cheaper", 0, "2.5", -100),
    )
    points, indices = matrix_points(start, offers, finish)
    assert points == (start, Coordinates(32, -101), finish)
    assert indices == (0, 2, 1, 0)


def test_larger_research_limit_keeps_more_distinct_points() -> None:
    offers = tuple(
        candidate(f"site-{index}", (index + 1) * 10, "3", -100 - index / 100)
        for index in range(49)
    )
    narrow = select_matrix_candidates((), offers, Decimal(1000), max_locations=40)
    normal = select_matrix_candidates((), offers, Decimal(1000))
    assert normal is not None and narrow is not None
    assert len(narrow) == 38
    assert len(normal) == 49
    with pytest.raises(ValueError, match="limits"):
        select_matrix_candidates((), offers, Decimal(1000), max_locations=60)


def test_time_only_ranking_does_not_let_far_cheap_sites_fill_matrix() -> None:
    near = candidate("near", 100, "3.20", -101)
    far = tuple(
        ProjectedCandidate(
            candidate(f"far-{index}", 100 + index, "3.00", -102 - index / 100).station,
            Decimal(100 + index),
            30 * METERS_PER_MILE,
            f"far-{index}:1",
        )
        for index in range(8)
    )
    chosen = select_matrix_candidates(
        (),
        (near, *far),
        Decimal(1000),
        max_locations=3,
        offset_price_penalty_per_mile=Decimal("0.02"),
    )
    assert chosen is not None
    assert [item.station.station_id for item in chosen] == ["near"]


def test_endpoint_adjacent_offers_remain_available_to_directed_search() -> None:
    behind_origin = candidate("near-origin", 0, "2", -100.001)
    beyond_finish = candidate("near-finish", 1000, "2.5", -102.001)
    interior = candidate("interior", 500, "4", -101)
    outside = replace(interior, progress_meters=Decimal(1001))
    chosen = select_matrix_candidates(
        (), (behind_origin, beyond_finish, interior, outside), Decimal(1000)
    )
    assert chosen is not None
    assert {item.station.station_id for item in chosen} == {
        "near-origin",
        "near-finish",
        "interior",
    }


def test_price_access_choices_and_later_alternatives_survive_early_cluster() -> None:
    early = tuple(
        candidate(f"early-{i}", 20 + i, "1", -100 - i / 100) for i in range(40)
    )
    late = tuple(candidate(f"late-{i}", 850 + i, "4", -102 - i / 100) for i in range(8))
    far_cheap = replace(
        candidate("late-cheap", 850, "2", -104), offset_meters=Decimal(50000)
    )
    chosen = select_matrix_candidates(
        (),
        (*early, *late, far_cheap),
        Decimal(1000),
        max_locations=12,
        offset_price_penalty_per_mile=Decimal("0.02"),
    )
    assert chosen is not None
    ids = {item.station.station_id for item in chosen}
    assert "late-cheap" in ids
    assert "late-0" in ids
    assert len(ids & {item.station.station_id for item in late}) >= 3


def test_starting_fuel_moves_the_first_bridge_window():
    offers = (
        candidate("cheap-cluster", 5 * METERS_PER_MILE, "1", -100),
        candidate("first-depletion", 48 * METERS_PER_MILE, "4", -101),
        candidate("later-depletion", 248 * METERS_PER_MILE, "4", -102),
    )
    first = select_matrix_candidates(
        (),
        offers,
        1200 * METERS_PER_MILE,
        max_locations=3,
        initial_gallons=Decimal(10),
        reserve_gallons=Decimal(5),
    )
    later = select_matrix_candidates(
        (),
        offers,
        1200 * METERS_PER_MILE,
        max_locations=3,
        initial_gallons=Decimal(30),
        reserve_gallons=Decimal(5),
    )
    assert [v.station.station_id for v in first] == ["first-depletion"]
    assert [v.station.station_id for v in later] == ["later-depletion"]
