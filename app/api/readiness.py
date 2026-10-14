"""Validate real application prerequisites without contacting routing providers."""

import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from decimal import DecimalException
from pathlib import Path
from threading import Lock
from time import monotonic

from django.conf import settings
from django.db import DatabaseError, connection
from shapely.errors import GEOSException

from app.api.assets import load_boundaries, load_candidates
from app.api.deadline import planning_activity
from app.api.models import TripResult
from app.api.routing_config import national_local_config
from app.locations.models import GazetteerVersion, Place, PlaceAlias
from app.planning.settings import planner_settings
from app.stations.accounting import REQUIRED_CSV_SITE_COVERAGE_PERCENT
from app.stations.models import DatasetVersion, Station

logger = logging.getLogger("app.api.operations")
_guard = Lock()
_verified_key = None
_verified_until = 0.0
LOCATION_FILES = ("states_coarse.geojson", "states_detailed.geojson", "places.ndjson")


def _fingerprint(paths):
    return tuple(
        (str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino)
        for path in paths
        for info in [path.stat()]
    )


def _coverage(manifest, dataset):
    us = dataset.counts["us_station_ids"]
    non_us = manifest["total_non_us_station_ids"]
    return (
        us > 0
        and manifest["source_sha256"] == dataset.source_sha256
        and manifest["candidate_count"] == manifest["total_us_station_ids"] == us
        and manifest["total_csv_station_ids"] == us + non_us
        and manifest["linked_csv_station_ids"]
        == manifest["candidate_count"] + manifest["non_us_linked_station_ids"]
        == manifest["total_csv_station_ids"]
        and REQUIRED_CSV_SITE_COVERAGE_PERCENT
        <= manifest["csv_coverage_percent"]
        <= 100
    )


def _validate_assets(folder, manifest, dataset, gazetteer):
    location_manifest = json.loads((folder / "manifest.json").read_text())
    outputs = location_manifest["outputs"]
    if (
        location_manifest["sources"]["geonames_us"]["sha256"] != gazetteer.source_sha256
        or outputs["states_detailed.geojson"]["sha256"] != gazetteer.boundary_sha256
        or location_manifest["counts"]["populated_places"] != gazetteer.place_count
    ):
        raise ValueError("gazetteer identity mismatch")
    for name in LOCATION_FILES:
        path = folder / name
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != outputs[name]["sha256"]:
            raise ValueError("location asset hash mismatch")
    load_boundaries()
    candidate_file = Path(settings.BASE_DIR) / "var/station_candidates/candidates.json"
    if (
        hashlib.sha256(candidate_file.read_bytes()).hexdigest()
        != manifest["candidate_sha256"]
    ):
        raise ValueError("candidate asset hash mismatch")
    index = load_candidates(dataset.source_sha256, manifest["candidate_sha256"])
    prices = dict(
        Station.objects.filter(dataset=dataset, country_code="US").values_list(
            "opis_id", "selected_price"
        )
    )
    if prices != {
        station.station_id: station.price_per_gallon for station in index.stations
    }:
        raise ValueError("station records differ from prepared offers")
    if index.version != manifest["candidate_sha256"]:
        raise ValueError("candidate identity changed during readiness")


def _local_road_ready(manifest, config):
    route = national_local_config(manifest["candidate_sha256"], config)
    if route is None:
        return False
    _, version, path = route
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.25)
    ) as db:
        metadata = dict(db.execute("SELECT key,value FROM metadata"))
        if metadata.get("road_graph_version") != version:
            return False
        if metadata.get("schema") == "csv-station-edges-v1-auto":
            if metadata.get("candidate_hash") != manifest["candidate_sha256"]:
                return False
            clause = ""
        else:
            from app.planning.road_cache_identity import SCHEMA_VERSION

            if metadata.get("schema") != SCHEMA_VERSION:
                return False
            clause = " WHERE point.active = 1"
        points = db.execute("SELECT COUNT(*) FROM point" + clause).fetchone()[0]
        complete = db.execute(
            "SELECT COUNT(*) FROM complete_source JOIN point USING(point_id)" + clause
        ).fetchone()[0]
        db.execute(
            "SELECT source_id,target_id,distance_meters,duration_seconds "
            "FROM edge LIMIT 1"
        )
        return points > 0 and complete == points


def validate_prerequisites(*, full=False):
    """Cheap live checks; hash/load checks on change, startup, and every 30s.

    Cache only successful integrity checks. Each poll still checks file presence,
    dataset identities, prepared DB rows, configuration and draining state.
    """
    global _verified_key, _verified_until
    if not planning_activity()["accepting_work"]:
        raise ValueError("planning is draining")
    config = planner_settings()
    connection.ensure_connection()
    datasets = list(DatasetVersion.objects.filter(status="active")[:2])
    gazetteers = list(GazetteerVersion.objects.filter(status="active")[:2])
    if len(datasets) != 1 or len(gazetteers) != 1:
        raise ValueError("one active price dataset and gazetteer are required")
    dataset, gazetteer = datasets[0], gazetteers[0]
    root = Path(settings.BASE_DIR)
    candidates = root / "var/station_candidates"
    folder = root / "data/prepared/locations"
    manifest = json.loads((candidates / "manifest.json").read_text())
    if not _coverage(manifest, dataset):
        raise ValueError("station coverage differs from active dataset")
    if (
        gazetteer.place_count < 1
        or Place.objects.filter(version=gazetteer).count() != gazetteer.place_count
        or not PlaceAlias.objects.filter(place__version=gazetteer).exists()
        or Station.objects.filter(dataset=dataset, country_code="US").count()
        != manifest["candidate_count"]
    ):
        raise ValueError("prepared database records are incomplete")
    # Query the saved-result table even when there are no existing trips.
    TripResult.objects.values(
        "public_id", "token_hash", "payload", "expires_at"
    ).first()
    if not config.ors_api_key and not (
        config.national_local_router and _local_road_ready(manifest, config)
    ):
        raise ValueError("no configured routing strategy")
    paths = [candidates / "manifest.json", candidates / "candidates.json"]
    paths += [folder / "manifest.json", *(folder / name for name in LOCATION_FILES)]
    key = (
        dataset.pk,
        gazetteer.pk,
        dataset.source_sha256,
        gazetteer.source_sha256,
        gazetteer.boundary_sha256,
        gazetteer.place_count,
        _fingerprint(paths),
    )
    with _guard:
        if full or key != _verified_key or monotonic() >= _verified_until:
            _validate_assets(folder, manifest, dataset, gazetteer)
            if _fingerprint(paths) != key[-1]:
                raise ValueError("prepared assets changed during validation")
            _verified_key, _verified_until = key, monotonic() + 30
    if not planning_activity()["accepting_work"]:
        raise ValueError("planning is draining")


def application_ready(*, full=False):
    try:
        validate_prerequisites(full=full)
    except (
        DatabaseError,
        sqlite3.Error,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        DecimalException,
        GEOSException,
    ):
        logger.error("api_readiness_failed")
        return False
    return True
