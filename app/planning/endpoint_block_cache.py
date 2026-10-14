"""Persistent, versioned cache for exact local endpoint road blocks."""

import hashlib
import json
import os
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.routing.local_valhalla import LocalRoadBlock, LocalValhalla


class EndpointBlockCache:
    """Reuse successful directed Valhalla blocks for matching coordinates."""

    def __init__(self, folder: Path, local: LocalValhalla) -> None:
        self.folder = folder
        self.local = local

    def _path(
        self, sources: tuple[Coordinates, ...], targets: tuple[Coordinates, ...]
    ) -> Path:
        request = {
            "schema": 1,
            "profile": "valhalla-auto-fastest",
            "graph_version": self.local.graph_version,
            "sources": [(point.latitude, point.longitude) for point in sources],
            "targets": [(point.latitude, point.longitude) for point in targets],
        }
        digest = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return self.folder / f"{digest}.json"

    @staticmethod
    def _read(path: Path, rows: int, columns: int) -> LocalRoadBlock | None:
        try:
            if path.stat().st_size > 100_000:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema") != 1:
                return None

            def parse(name: str) -> tuple[tuple[Decimal | None, ...], ...]:
                raw = payload[name]
                if not isinstance(raw, list) or len(raw) != rows:
                    raise ValueError("invalid cache rows")
                parsed: list[tuple[Decimal | None, ...]] = []
                for row in raw:
                    if not isinstance(row, list) or len(row) != columns:
                        raise ValueError("invalid cache columns")
                    values: list[Decimal | None] = []
                    for value in row:
                        if value is None:
                            values.append(None)
                        elif isinstance(value, str):
                            number = Decimal(value)
                            if not number.is_finite() or number < 0:
                                raise ValueError("invalid cached road value")
                            values.append(number)
                        else:
                            raise ValueError("invalid cached road value")
                    parsed.append(tuple(values))
                return tuple(parsed)

            distances = parse("distances_meters")
            durations = parse("durations_seconds")
            if any(
                (distance is None) != (duration is None)
                for distance_row, duration_row in zip(distances, durations, strict=True)
                for distance, duration in zip(distance_row, duration_row, strict=True)
            ):
                return None
            return LocalRoadBlock(distances, durations)
        except (OSError, KeyError, TypeError, ValueError, InvalidOperation):
            return None

    @staticmethod
    def _write(path: Path, block: LocalRoadBlock) -> None:
        payload = {
            "schema": 1,
            "distances_meters": [
                [None if value is None else str(value) for value in row]
                for row in block.distances_meters
            ],
            "durations_seconds": [
                [None if value is None else str(value) for value in row]
                for row in block.durations_seconds
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, separators=(",", ":"))
            os.replace(temporary, path)
        except OSError:
            pass  # A cache-write failure must not invalidate a valid road response.
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def matrix_block(
        self,
        sources: tuple[Coordinates, ...],
        targets: tuple[Coordinates, ...],
        budget: RequestBudget,
    ) -> LocalRoadBlock:
        budget.check_deadline()
        path = self._path(sources, targets)
        cached = self._read(path, len(sources), len(targets))
        if cached is not None:
            return cached
        block = self.local.matrix_block(sources, targets, budget)
        self._write(path, block)
        return block
