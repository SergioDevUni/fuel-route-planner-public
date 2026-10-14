"""The final road marker must agree with the checkpoint and CSV snapshot."""

import json
import sqlite3
from contextlib import closing

import pytest

from app.domain.routing import Coordinates
from app.planning.road_cache_identity import SCHEMA_VERSION, point_set_hash
from scripts import check_national_activation as activation


def test_activation_gate_checks_marker_and_complete_source_count(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "edges.db"
    marker = tmp_path / "edges.complete.json"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"candidate_sha256": "csv-v1", "source_sha256": "source-v1"})
    )
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executescript(
            "CREATE TABLE metadata(key TEXT, value TEXT);"
            "CREATE TABLE point(point_id INTEGER, snap_meters REAL);"
            "CREATE TABLE complete_source(point_id INTEGER);"
            "CREATE TABLE edge(source_id INTEGER, target_id INTEGER);"
            "INSERT INTO metadata VALUES ('candidate_hash','csv-v1');"
            "INSERT INTO metadata VALUES ('source_hash','source-v1');"
        )
        connection.execute(
            "INSERT INTO metadata VALUES ('road_graph_version',?)",
            (activation.ROAD_VERSION,),
        )
        connection.execute("INSERT INTO point VALUES (1, 2)")
        connection.execute("INSERT INTO complete_source VALUES (1)")
        connection.execute("INSERT INTO edge VALUES (1, 1)")
    monkeypatch.setattr(activation, "DATABASE", database)
    monkeypatch.setattr(activation, "MARKER", marker)
    monkeypatch.setattr(activation, "MANIFEST", manifest)
    assert activation.inspect()["status"] == "pending"
    marker.write_text(
        json.dumps(
            {
                "candidate_version": "csv-v1",
                "source_hash": "source-v1",
                "graph_version": activation.ROAD_VERSION,
                "completed_sources": 1,
                "stored_directed_legs": 1,
            }
        )
    )
    assert activation.inspect()["status"] == "cache_complete"
    marker.write_text(
        json.dumps(
            {
                "candidate_version": "other",
                "source_hash": "source-v1",
                "graph_version": activation.ROAD_VERSION,
                "completed_sources": 1,
                "stored_directed_legs": 1,
            }
        )
    )
    with pytest.raises(RuntimeError, match="completion marker"):
        activation.inspect()


def test_update_safe_activation_uses_points_instead_of_offer_version(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "edges_v2.db"
    marker = tmp_path / "edges_v2.complete.json"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"candidate_sha256": "new-price", "source_sha256": "new-csv"})
    )
    point_hash = point_set_hash((Coordinates(41.0, -87.0),))
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executescript(
            "CREATE TABLE metadata(key TEXT, value TEXT);"
            "CREATE TABLE point(point_id INTEGER, latitude REAL, longitude REAL, "
            "snap_meters REAL, active INTEGER);"
            "CREATE TABLE complete_source(point_id INTEGER);"
            "CREATE TABLE edge(source_id INTEGER, target_id INTEGER);"
            "INSERT INTO point VALUES (0,41.0,-87.0,1,1);"
            "INSERT INTO complete_source VALUES (0);"
        )
        connection.executemany(
            "INSERT INTO metadata VALUES (?,?)",
            (
                ("schema", SCHEMA_VERSION),
                ("road_graph_version", activation.ROAD_VERSION),
                ("active_point_hash", point_hash),
            ),
        )
    monkeypatch.setattr(activation, "DATABASE", database)
    monkeypatch.setattr(activation, "MARKER", marker)
    monkeypatch.setattr(activation, "MANIFEST", manifest)
    marker.write_text(
        json.dumps(
            {
                "active_point_hash": point_hash,
                "graph_version": activation.ROAD_VERSION,
                "completed_sources": 1,
                "stored_directed_legs": 0,
            }
        )
    )
    assert activation.inspect()["status"] == "cache_complete"
