"""Validated hosted, regional and national routing settings."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

from django.conf import settings

from app.api.assets import current_road_point_hash
from app.planning.road_cache_identity import SCHEMA_VERSION as UPDATE_SAFE_SCHEMA
from app.planning.settings import PlannerSettings, planner_settings

logger = logging.getLogger("app.api.service")


def road_database(config: PlannerSettings | None = None) -> Path:
    configured = (config or planner_settings()).road_database
    if configured:
        return Path(configured)
    folder = Path(settings.BASE_DIR) / "var/road_nationwide"
    update_safe = folder / "station_edges_v2_live.db"
    return update_safe if update_safe.is_file() else folder / "station_edges.db"


def national_local_config(
    candidate_hash: str, config: PlannerSettings | None = None
) -> tuple[str, str, Path] | None:
    """Return the local router only for a completed matching CSV/road version."""
    config = config or planner_settings()
    url, version = config.valhalla_url, config.valhalla_version
    database = road_database(config)
    marker = database.with_suffix(".complete.json")
    if not url or not version or not database.is_file() or not marker.is_file():
        return None
    ready = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(ready, dict):
        return None
    if ready.get("graph_version") != version:
        return None
    if ready.get("schema") == UPDATE_SAFE_SCHEMA:
        if ready.get("active_point_hash") != current_road_point_hash(candidate_hash):
            return None
    elif ready.get("candidate_version") != candidate_hash:
        return None
    return url, version, database


def partial_road_config(
    candidate_hash: str, config: PlannerSettings | None = None
) -> tuple[str, str, Path, int] | None:
    """Allow hosted plans to improve from completed local source rows only."""
    config = config or planner_settings()
    if not config.partial_refiner:
        return None
    url, version = config.valhalla_url, config.valhalla_version
    database = road_database(config)
    if not url or not version or not database.is_file():
        return None
    try:
        with closing(
            sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        ) as db:
            metadata = dict(db.execute("SELECT key, value FROM metadata"))
            if metadata.get("road_graph_version") != version:
                return None
            if metadata.get("schema") == UPDATE_SAFE_SCHEMA:
                if metadata.get("active_point_hash") != current_road_point_hash(
                    candidate_hash
                ):
                    return None
                complete = db.execute(
                    "SELECT COUNT(*) FROM complete_source "
                    "JOIN point USING(point_id) WHERE point.active = 1"
                ).fetchone()[0]
            elif (
                metadata.get("schema") == "csv-station-edges-v1-auto"
                and metadata.get("candidate_hash") == candidate_hash
            ):
                complete = db.execute(
                    "SELECT COUNT(*) FROM complete_source"
                ).fetchone()[0]
            else:
                return None
    except sqlite3.Error:
        return None
    return (url, version, database, complete) if complete else None


def regional_config(
    config: PlannerSettings | None = None,
) -> tuple[str, str, tuple[float, float, float, float]] | None:
    config = config or planner_settings()
    if not config.osrm_url:
        return None
    if config.osrm_bounds is None:
        raise ValueError("regional OSRM bounds are missing")
    return config.osrm_url, config.osrm_version, config.osrm_bounds
