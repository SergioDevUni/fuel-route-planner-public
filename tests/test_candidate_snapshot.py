import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.routing import Coordinates, Route, RouteLeg, RouteWaypoint
from app.planning.candidates import CandidateIndex
from tests.support.geometry import _route


def test_projection_cache_is_scoped_to_snapshot_prices_and_distinct_offers(tmp_path):
    import hashlib

    def snapshot(prices, source):
        items = [
            {
                "station_id": str(i),
                "name": "Site",
                "address": "Main",
                "city": "City",
                "state": "IL",
                "latitude": 41.0005,
                "longitude": -87.9,
                "price_per_gallon": price,
            }
            for i, price in enumerate(prices)
        ]
        raw = json.dumps(items).encode()
        (tmp_path / "candidates.json").write_bytes(raw)
        (tmp_path / "manifest.json").write_text(
            json.dumps(
                {
                    "source_sha256": source,
                    "candidate_sha256": hashlib.sha256(raw).hexdigest(),
                    "candidate_count": len(items),
                }
            )
        )
        return CandidateIndex(tmp_path, source)

    route = _route()
    old = snapshot(("3", "4"), "old")
    found = old.along_route(route, 100)
    assert old.along_route(route, 100) is found
    new = snapshot(("5", "6"), "new")
    refreshed = new.along_route(route, 100)
    assert {v.station.station_id for v in refreshed} == {"0", "1"}
    assert {v.station.price_per_gallon for v in refreshed} == {Decimal(5), Decimal(6)}
    assert {v.station.price_per_gallon for v in found} == {Decimal(3), Decimal(4)}
    from app.planning.budget import RequestBudget
    from app.planning.candidates import discovery_context

    budget = RequestBudget()
    with discovery_context(budget, "test"):
        new.along_route(route, 20)
        new.within_detour_envelope(route, route.distance_meters)
        new.within_detour_envelope(route, route.distance_meters + 1000)
    assert budget.metrics["test_endpoint_distance_builds"] == 1
    assert "test_station_position_builds" not in budget.metrics
    assert all(not values.flags.writeable for values in new._positions)
    assert new._positions is not old._positions


def test_candidate_projection_uses_meters_and_route_progress(tmp_path: Path) -> None:
    import hashlib

    items = [
        {
            "station_id": "1",
            "name": "Test",
            "address": "Main",
            "city": "Chicago",
            "state": "IL",
            "latitude": 41.0005,
            "longitude": -87.9,
            "price_per_gallon": "3.50",
        }
    ]
    raw = json.dumps(items).encode()
    (tmp_path / "candidates.json").write_bytes(raw)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "source_sha256": "abc",
                "candidate_sha256": hashlib.sha256(raw).hexdigest(),
                "candidate_count": 1,
            }
        )
    )
    index = CandidateIndex(tmp_path, "abc")
    found = index.along_route(_route(), corridor_meters=100)
    assert len(found) == 1
    assert 50 < found[0].offset_meters < 60
    assert Decimal(9000) < found[0].progress_meters < Decimal(11000)
    assert index.along_route(_route(), corridor_meters=20) == ()
    with pytest.raises(ValueError, match="active price data"):
        CandidateIndex(tmp_path, "other")


def test_projection_keeps_separate_loop_encounters(tmp_path: Path) -> None:
    import hashlib

    item = {
        "station_id": "1",
        "name": "Test",
        "address": "Main",
        "city": "City",
        "state": "IL",
        "latitude": 41.0,
        "longitude": -87.9,
        "price_per_gallon": "3.50",
    }
    raw = json.dumps([item]).encode()
    (tmp_path / "candidates.json").write_bytes(raw)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "source_sha256": "abc",
                "candidate_sha256": hashlib.sha256(raw).hexdigest(),
                "candidate_count": 1,
            }
        )
    )
    points = (
        Coordinates(41.0, -87.9),
        Coordinates(41.0, -87.8),
        Coordinates(41.0, -87.7),
        Coordinates(41.0, -87.9),
    )
    route = Route(
        geometry=points,
        waypoints=(
            RouteWaypoint(points[0], points[0], 0, Decimal(0)),
            RouteWaypoint(points[-1], points[-1], 3, Decimal(0)),
        ),
        legs=(RouteLeg(Decimal(40000), Decimal(1000)),),
        distance_meters=Decimal(40000),
        duration_seconds=Decimal(1000),
        engine_version=None,
        fetched_at=datetime.now(UTC),
    )
    found = CandidateIndex(tmp_path, "abc").along_route(route, corridor_meters=100)
    assert [item.occurrence_id for item in found] == ["1:1", "1:2"]


def test_same_coordinate_retains_distinct_station_prices(tmp_path: Path) -> None:
    import hashlib

    records = [
        {
            "station_id": station_id,
            "name": f"Station {station_id}",
            "address": "Same exit",
            "city": "Chicago",
            "state": "IL",
            "latitude": 41.0,
            "longitude": -87.9,
            "price_per_gallon": price,
        }
        for station_id, price in (("101", "4.00"), ("202", "3.00"))
    ]
    raw = json.dumps(records).encode()
    (tmp_path / "candidates.json").write_bytes(raw)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "source_sha256": "abc",
                "candidate_sha256": hashlib.sha256(raw).hexdigest(),
                "candidate_count": 2,
            }
        )
    )
    index = CandidateIndex(tmp_path, "abc")
    assert [(s.station_id, s.price_per_gallon) for s in index.stations] == [
        ("101", Decimal("4.00")),
        ("202", Decimal("3.00")),
    ]
    projected = index.along_route(_route(), corridor_meters=100)
    assert [item.station.station_id for item in projected] == ["101", "202"]
    assert projected[0].progress_meters == projected[1].progress_meters


def test_detour_envelope_finds_off_route_site_and_rejects_impossible_one(
    tmp_path: Path,
) -> None:
    import hashlib

    records = [
        {
            "station_id": station_id,
            "name": station_id,
            "address": "Main",
            "city": "Chicago",
            "state": "IL",
            "latitude": latitude,
            "longitude": -87.9,
            "price_per_gallon": "3.50",
        }
        for station_id, latitude in (("alternate", 41.05), ("too-far", 42.0))
    ]
    raw = json.dumps(records).encode()
    (tmp_path / "candidates.json").write_bytes(raw)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "source_sha256": "abc",
                "candidate_sha256": hashlib.sha256(raw).hexdigest(),
                "candidate_count": len(records),
            }
        )
    )
    index = CandidateIndex(tmp_path, "abc")
    route = _route()
    assert index.along_route(route, 100) == ()
    found = index.within_detour_envelope(route, Decimal(25000))
    assert [item.station.station_id for item in found] == ["alternate"]
    assert 0 < found[0].progress_meters < route.distance_meters
    with pytest.raises(ValueError, match="shorter"):
        index.within_detour_envelope(route, Decimal(19000))


def test_envelope_keeps_distinct_offers_exactly_at_endpoints(tmp_path: Path) -> None:
    import hashlib

    route = _route()
    records = [
        {
            "station_id": station_id,
            "name": station_id,
            "address": "Main",
            "city": "Example",
            "state": "IL",
            "latitude": point.latitude,
            "longitude": point.longitude,
            "price_per_gallon": price,
        }
        for station_id, point, price in (
            ("origin", route.waypoints[0].requested, "3"),
            ("origin-other-offer", route.waypoints[0].requested, "2"),
            ("finish", route.waypoints[-1].requested, "4"),
        )
    ]
    raw = json.dumps(records).encode()
    (tmp_path / "candidates.json").write_bytes(raw)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "source_sha256": "abc",
                "candidate_sha256": hashlib.sha256(raw).hexdigest(),
                "candidate_count": len(records),
            }
        )
    )
    found = CandidateIndex(tmp_path, "abc").within_detour_envelope(
        route, Decimal(25000)
    )
    assert [item.station.station_id for item in found] == [
        "origin",
        "origin-other-offer",
        "finish",
    ]
    assert [item.progress_meters for item in found] == [
        Decimal(0),
        Decimal(0),
        route.distance_meters,
    ]
