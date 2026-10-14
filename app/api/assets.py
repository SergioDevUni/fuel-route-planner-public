"""Versioned place and station asset loading."""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from app.locations.boundary import BoundaryIndex
from app.planning.asset_cache import ImmutableAssetCache
from app.planning.budget import RequestBudget
from app.planning.candidates import CandidateIndex

logger = logging.getLogger("app.api.service")


_boundary_assets = ImmutableAssetCache[BoundaryIndex]("boundary", max_entries=1)


_candidate_assets = ImmutableAssetCache[CandidateIndex]("candidate", max_entries=4)


def load_boundaries(budget: RequestBudget | None = None) -> BoundaryIndex:
    folder = Path(settings.BASE_DIR) / "data/prepared/locations"
    version = hashlib.sha256((folder / "manifest.json").read_bytes()).hexdigest()
    return _boundary_assets.get(
        (folder, version), lambda: BoundaryIndex(folder), budget
    )


def load_candidates(
    source_hash: str, candidate_hash: str, budget: RequestBudget | None = None
) -> CandidateIndex:
    folder = Path(settings.BASE_DIR) / "var/station_candidates"

    def load() -> CandidateIndex:
        index = CandidateIndex(folder, source_hash)
        if index.version != candidate_hash:
            raise ValueError("candidate snapshot changed during request")
        return index

    return _candidate_assets.get((folder, source_hash, candidate_hash), load, budget)


def current_candidate_hash() -> str:
    path = Path(settings.BASE_DIR) / "var/station_candidates/manifest.json"
    return json.loads(path.read_text())["candidate_sha256"]


@lru_cache(maxsize=4)
def current_road_point_hash(candidate_hash: str) -> str:
    manifest = json.loads(
        (Path(settings.BASE_DIR) / "var/station_candidates/manifest.json").read_text()
    )
    return load_candidates(manifest["source_sha256"], candidate_hash).road_point_hash
