"""Road progress survives mutable fuel offers and coordinate additions."""

import sqlite3

from app.domain.routing import Coordinates
from app.planning.national_graph import completed_road_points
from app.planning.road_cache_identity import SCHEMA_VERSION, point_set_hash
from scripts.migrate_station_edges_v2 import migrate
from scripts.road_precompute.storage import (
    _connect,
    _initialize,
    _reconcile_update_safe_points,
)


def test_shadow_migration_preserves_edges_and_price_change(tmp_path) -> None:
    source = tmp_path / "old.db"
    shadow = tmp_path / "new.db"
    chicago = Coordinates(41.8781, -87.6298)
    joliet = Coordinates(41.525, -88.0817)
    points = (chicago, joliet)
    connection = _connect(source)
    try:
        _initialize(connection, points, "old-csv", "old-priced-snapshot", "road-v1")
        with connection:
            connection.execute("UPDATE point SET snap_meters=1")
            connection.execute("INSERT INTO edge VALUES (0, 1, 60000, 3600)")
            connection.execute("INSERT INTO complete_source VALUES (0)")
    finally:
        connection.close()

    report = migrate(source, shadow)
    assert report["points"] == 2
    assert report["completed_sources"] == 1
    assert report["directed_edges"] == 1
    with sqlite3.connect(shadow) as updated:
        metadata = dict(updated.execute("SELECT key, value FROM metadata"))
        assert metadata["schema"] == SCHEMA_VERSION
        assert "candidate_hash" not in metadata
        assert "source_hash" not in metadata
        assert _reconcile_update_safe_points(updated, points, "road-v1") == points
        assert updated.execute("SELECT COUNT(*) FROM complete_source").fetchone() == (
            1,
        )
        assert updated.execute("SELECT COUNT(*) FROM edge").fetchone() == (1,)
    assert completed_road_points(
        shadow,
        "new-price-and-name-snapshot",
        "road-v1",
        active_point_hash=point_set_hash(points),
    ) == {chicago}


def test_added_point_appends_id_and_reopens_only_nearby_sources(tmp_path) -> None:
    source = tmp_path / "old.db"
    shadow = tmp_path / "new.db"
    chicago = Coordinates(41.8781, -87.6298)
    joliet = Coordinates(41.525, -88.0817)
    los_angeles = Coordinates(34.0522, -118.2437)
    new_chicago = Coordinates(41.9, -87.6)
    old = (chicago, joliet, los_angeles)
    connection = _connect(source)
    try:
        _initialize(connection, old, "csv", "snapshot", "road-v1")
        with connection:
            connection.execute("UPDATE point SET snap_meters=1")
            connection.executemany(
                "INSERT INTO complete_source VALUES (?)", ((0,), (1,), (2,))
            )
            connection.execute("INSERT INTO edge VALUES (0, 1, 60000, 3600)")
    finally:
        connection.close()
    migrate(source, shadow)
    with sqlite3.connect(shadow) as updated:
        all_points = _reconcile_update_safe_points(
            updated, old + (new_chicago,), "road-v1"
        )
        assert all_points == old + (new_chicago,)
        assert list(updated.execute("SELECT point_id FROM complete_source")) == [(2,)]
        assert updated.execute("SELECT COUNT(*) FROM edge").fetchone() == (1,)
        assert updated.execute(
            "SELECT active FROM point WHERE point_id=3"
        ).fetchone() == (1,)
        _reconcile_update_safe_points(
            updated, (chicago, los_angeles, new_chicago), "road-v1"
        )
        assert updated.execute(
            "SELECT active FROM point WHERE point_id=1"
        ).fetchone() == (0,)
        assert list(updated.execute("SELECT point_id FROM complete_source")) == [(2,)]


def test_partial_service_accepts_new_offer_version_at_same_points(
    tmp_path, monkeypatch
) -> None:
    import django

    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    from app.api import routing_config as service

    source = tmp_path / "old.db"
    shadow = tmp_path / "new.db"
    point = Coordinates(41.8781, -87.6298)
    connection = _connect(source)
    try:
        _initialize(connection, (point,), "old-csv", "old-price", "road-v1")
        with connection:
            connection.execute("UPDATE point SET snap_meters=1")
            connection.execute("INSERT INTO complete_source VALUES (0)")
    finally:
        connection.close()
    migrate(source, shadow)
    monkeypatch.setattr(service, "road_database", lambda _config=None: shadow)
    monkeypatch.setattr(
        service, "current_road_point_hash", lambda _: point_set_hash((point,))
    )
    monkeypatch.setenv("FUEL_PARTIAL_ROAD_REFINER", "1")
    monkeypatch.setenv("FUEL_NATIONAL_VALHALLA_URL", "http://localhost:8005")
    monkeypatch.setenv("FUEL_NATIONAL_VALHALLA_VERSION", "road-v1")
    assert service.partial_road_config("new-price") == (
        "http://localhost:8005",
        "road-v1",
        shadow,
        1,
    )
