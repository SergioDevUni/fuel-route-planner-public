import sqlite3
from pathlib import Path

from app.domain.routing import Coordinates
from app.planning.road_cache_identity import SCHEMA_VERSION as UPDATE_SAFE_SCHEMA
from app.planning.road_cache_identity import point_set_hash
from scripts.road_precompute.policy import GEOD, MAX_LEG_METERS, SCHEMA_VERSION


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS point (
            point_id INTEGER PRIMARY KEY,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            snap_meters REAL,
            snap_error TEXT
        );
        CREATE TABLE IF NOT EXISTS edge (
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            distance_meters REAL NOT NULL,
            duration_seconds REAL NOT NULL,
            PRIMARY KEY (source_id, target_id)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS complete_source (
            point_id INTEGER PRIMARY KEY
        );
        """
    )
    return connection


def _initialize(
    connection: sqlite3.Connection,
    points: tuple[Coordinates, ...],
    source_hash: str,
    candidate_hash: str,
    graph_version: str,
) -> None:
    expected = {
        "schema": SCHEMA_VERSION,
        "source_hash": source_hash,
        "candidate_hash": candidate_hash,
        "road_graph_version": graph_version,
        "profile": "valhalla-auto",
        "max_leg_meters": str(MAX_LEG_METERS),
    }
    actual = dict(connection.execute("SELECT key, value FROM metadata"))
    if actual and (
        {k: v for k, v in actual.items() if k != "target_screen_policy"} != expected
    ):
        raise ValueError("road graph database belongs to another CSV or road version")
    if not actual:
        with connection:
            connection.executemany(
                "INSERT INTO metadata (key, value) VALUES (?, ?)", expected.items()
            )
            connection.executemany(
                "INSERT INTO point (point_id, latitude, longitude) VALUES (?, ?, ?)",
                (
                    (index, point.latitude, point.longitude)
                    for index, point in enumerate(points)
                ),
            )
    elif connection.execute("SELECT COUNT(*) FROM point").fetchone()[0] != len(points):
        raise ValueError("road graph database point count does not match CSV snapshot")


def _reconcile_update_safe_points(
    connection: sqlite3.Connection,
    current_points: tuple[Coordinates, ...],
    graph_version: str,
) -> tuple[Coordinates, ...]:
    """Retain point IDs; append new coordinates and reopen affected sources."""
    metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    if (
        metadata.get("schema") != UPDATE_SAFE_SCHEMA
        or metadata.get("road_graph_version") != graph_version
        or metadata.get("profile") != "valhalla-auto"
        or metadata.get("max_leg_meters") != str(MAX_LEG_METERS)
    ):
        raise ValueError("road graph database belongs to another road version")
    rows = list(
        connection.execute(
            "SELECT point_id, latitude, longitude, active FROM point ORDER BY point_id"
        )
    )
    if [row[0] for row in rows] != list(range(len(rows))):
        raise ValueError("road point IDs must be stable and contiguous")
    by_coordinate = {
        (latitude, longitude): point_id for point_id, latitude, longitude, _ in rows
    }
    wanted = {(point.latitude, point.longitude) for point in current_points}
    new_or_reactivated: list[tuple[int, Coordinates]] = []
    with connection:
        for point_id, latitude, longitude, active in rows:
            should_be_active = int((latitude, longitude) in wanted)
            if active != should_be_active:
                connection.execute(
                    "UPDATE point SET active=? WHERE point_id=?",
                    (should_be_active, point_id),
                )
                if should_be_active:
                    new_or_reactivated.append(
                        (point_id, Coordinates(latitude, longitude))
                    )
        for latitude, longitude in sorted(wanted - by_coordinate.keys()):
            point_id = len(rows)
            connection.execute(
                "INSERT INTO point(point_id, latitude, longitude, active) "
                "VALUES (?, ?, ?, 1)",
                (point_id, latitude, longitude),
            )
            rows.append((point_id, latitude, longitude, 1))
            new_or_reactivated.append((point_id, Coordinates(latitude, longitude)))
        if new_or_reactivated:
            # A complete source needs a checked leg to each newly active,
            # range-reachable point. Reopen only sources in that range; keep
            # their previously computed edges while the builder refreshes.
            for point_id, latitude, longitude, active in rows:
                if not active:
                    continue
                source = Coordinates(latitude, longitude)
                if any(
                    GEOD.inv(
                        source.longitude,
                        source.latitude,
                        added.longitude,
                        added.latitude,
                    )[2]
                    <= MAX_LEG_METERS + 1
                    for _, added in new_or_reactivated
                ):
                    connection.execute(
                        "DELETE FROM complete_source WHERE point_id=?", (point_id,)
                    )
        connection.execute(
            "UPDATE metadata SET value=? WHERE key='active_point_hash'",
            (point_set_hash(current_points),),
        )
    return tuple(Coordinates(latitude, longitude) for _, latitude, longitude, _ in rows)
