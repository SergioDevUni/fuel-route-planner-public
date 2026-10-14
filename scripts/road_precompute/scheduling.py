import json
import math
from pathlib import Path

from app.domain.routing import Coordinates
from app.planning.candidates import CandidateIndex
from scripts.road_precompute.policy import (
    GEOD,
    MAX_LEG_METERS,
    SOURCE_GROUP_CELL_DEGREES,
    TARGET_SNAP_ALLOWANCE_METERS,
)


def _points(folder: Path) -> tuple[tuple[Coordinates, ...], str, str, int]:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    index = CandidateIndex(folder, manifest["source_sha256"])
    points = tuple(
        sorted(
            {station.coordinates for station in index.stations},
            key=lambda point: (point.latitude, point.longitude),
        )
    )
    return points, manifest["source_sha256"], index.version, len(index.stations)


def _source_targets(
    sources: list[int],
    valid_points: list[int],
    points: tuple[Coordinates, ...],
) -> tuple[dict[int, set[int]], list[int]]:
    by_source: dict[int, set[int]] = {}
    targets: set[int] = set()
    for source in sources:
        start = points[source]
        nearby = set()
        for target in valid_points:
            finish = points[target]
            distance = GEOD.inv(
                start.longitude,
                start.latitude,
                finish.longitude,
                finish.latitude,
            )[2]
            # Both requested points can snap towards each other by up to 1km.
            # One extra meter protects the numerical tank boundary.
            if distance <= MAX_LEG_METERS + TARGET_SNAP_ALLOWANCE_METERS + 1:
                nearby.add(target)
        by_source[source] = nearby
        targets.update(nearby)
    return by_source, sorted(targets)


def _spatial_source_groups(
    source_ids: list[int],
    points: tuple[Coordinates, ...],
    batch_size: int,
    *,
    cell_degrees: float = SOURCE_GROUP_CELL_DEGREES,
) -> list[list[int]]:
    """Batch points within a bounded cell before a rectangular matrix query.

    Source cells of 0.5 degrees are under 80 km across in the US. A target
    within 500 miles of any source is then within 885 km of every source.
    Target cells of 5 degrees are under 790 km across even at the equator.
    Both sides must be grouped because the service checks all point pairs.
    """
    cells: dict[tuple[int, int], list[int]] = {}
    for source_id in source_ids:
        point = points[source_id]
        key = (
            math.floor(point.latitude / cell_degrees),
            math.floor(point.longitude / cell_degrees),
        )
        cells.setdefault(key, []).append(source_id)
    return [
        ids[offset : offset + batch_size]
        for key in sorted(cells)
        for ids in (cells[key],)
        for offset in range(0, len(ids), batch_size)
    ]


def _priority_groups(
    remaining: list[int],
    points: tuple[Coordinates, ...],
    batch_size: int,
    priority_ids: set[int],
) -> tuple[list[list[int]], int]:
    """Run requested source checkpoints first, then resume the ordinary queue."""
    priority = [point_id for point_id in remaining if point_id in priority_ids]
    rest = [point_id for point_id in remaining if point_id not in priority_ids]
    groups = _spatial_source_groups(priority, points, batch_size)
    priority_group_count = len(groups)
    groups.extend(_spatial_source_groups(rest, points, batch_size))
    return groups, priority_group_count


def _read_priority_file(
    path: Path | None,
    candidate_hash: str,
    graph_version: str,
    point_count: int,
    *,
    stable_points: bool = False,
) -> set[int]:
    if path is None:
        return set()
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or (not stable_points and document.get("candidate_version") != candidate_hash)
        or document.get("graph_version") != graph_version
        or not isinstance(document.get("point_ids"), list)
    ):
        raise ValueError("priority file differs from CSV or road graph version")
    ids = document["point_ids"]
    if any(
        type(point_id) is not int or not 0 <= point_id < point_count for point_id in ids
    ):
        raise ValueError("priority file contains an invalid road point ID")
    return set(ids)
