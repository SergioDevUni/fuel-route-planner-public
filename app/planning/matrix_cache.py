"""Short-lived, versioned cache for exact directed ORS matrix requests."""

import hashlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.domain.road import RoadDistanceMatrix
from app.domain.routing import Coordinates
from app.domain.units import finite_nonnegative
from app.planning.budget import RequestBudget
from app.planning.ports import MAX_HOSTED_MATRIX_LOCATIONS, RoadMatrixPort

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 2
PROFILE = "ors-fastest-driving-car-distance-duration"
MAX_CACHE_BYTES = 1_000_000


class CachedRoadMatrixProvider:
    """Cache one exact point set; provider data still expires after a short TTL."""

    def __init__(
        self,
        provider: RoadMatrixPort,
        folder: Path,
        *,
        cache_version: str = "v1",
        max_age_seconds: float = 6 * 60 * 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not cache_version or max_age_seconds <= 0:
            raise ValueError("matrix cache version and TTL are required")
        self.provider = provider
        self.folder = folder
        self.cache_version = cache_version
        self.max_age_seconds = max_age_seconds
        self.clock = clock

    def _key(self, points: tuple[Coordinates, ...]) -> str:
        request = {
            "schema": SCHEMA_VERSION,
            "profile": PROFILE,
            "cache_version": self.cache_version,
            "points": [point.as_provider_pair() for point in points],
        }
        return hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _read(self, path: Path, size: int) -> RoadDistanceMatrix | None:
        try:
            if path.stat().st_size > MAX_CACHE_BYTES:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            age = self.clock() - payload["created_at"]
            if (
                payload["schema"] != SCHEMA_VERSION
                or payload["profile"] != PROFILE
                or payload["cache_version"] != self.cache_version
                or not 0 <= age <= self.max_age_seconds
            ):
                return None

            def nonnegative(value: str) -> Decimal:
                if not isinstance(value, str):
                    raise ValueError("cached road value is not text")
                number = Decimal(value)
                finite_nonnegative(number, "cached road value")
                return number

            rows = payload["distances_meters"]
            if not isinstance(rows, list) or len(rows) != size:
                return None
            distances = tuple(
                tuple(None if value is None else nonnegative(value) for value in row)
                for row in rows
                if isinstance(row, list) and len(row) == size
            )
            if len(distances) != size:
                return None
            duration_rows = payload["durations_seconds"]
            if not isinstance(duration_rows, list) or len(duration_rows) != size:
                return None
            durations = tuple(
                tuple(None if value is None else nonnegative(value) for value in row)
                for row in duration_rows
                if isinstance(row, list) and len(row) == size
            )
            if len(durations) != size or any(
                (distance is None) != (duration is None)
                for distance_row, duration_row in zip(distances, durations, strict=True)
                for distance, duration in zip(distance_row, duration_row, strict=True)
            ):
                return None
            source = tuple(nonnegative(item) for item in payload["source_snap_meters"])
            destination = tuple(
                nonnegative(item) for item in payload["destination_snap_meters"]
            )
            if len(source) != size or len(destination) != size:
                return None
            return RoadDistanceMatrix(distances, source, destination, durations)
        except (OSError, KeyError, TypeError, ValueError, InvalidOperation, IndexError):
            return None

    def _write(self, path: Path, matrix: RoadDistanceMatrix) -> None:
        payload = {
            "schema": SCHEMA_VERSION,
            "profile": PROFILE,
            "cache_version": self.cache_version,
            "created_at": self.clock(),
            "distances_meters": [
                [None if value is None else str(value) for value in row]
                for row in matrix.distances_meters
            ],
            "durations_seconds": [
                [None if value is None else str(value) for value in row]
                for row in matrix.durations_seconds or ()
            ],
            "source_snap_meters": [str(value) for value in matrix.source_snap_meters],
            "destination_snap_meters": [
                str(value) for value in matrix.destination_snap_meters
            ],
        }
        temp_path: Path | None = None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.folder,
                prefix="matrix-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                json.dump(payload, stream, separators=(",", ":"))
            os.replace(temp_path, path)
        except OSError:
            logger.warning("matrix_cache_write_failed")
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("matrix_cache_temp_cleanup_failed")

    def matrix(
        self, points: tuple[Coordinates, ...], budget: RequestBudget
    ) -> RoadDistanceMatrix:
        if not 2 <= len(points) <= MAX_HOSTED_MATRIX_LOCATIONS:
            raise ValueError("matrix cache point count exceeds provider policy")
        budget.check_deadline()
        path = self.folder / f"{self._key(points)}.json"
        cached = self._read(path, len(points))
        if cached is not None:
            logger.info("matrix_cache_hit points=%d", len(points))
            return cached
        logger.info("matrix_cache_miss points=%d", len(points))
        matrix = self.provider.matrix(points, budget)
        self._write(path, matrix)
        return matrix
