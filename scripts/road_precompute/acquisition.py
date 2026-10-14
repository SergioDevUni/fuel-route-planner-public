import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import Executor
from decimal import Decimal

from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.routing.local_valhalla import (
    LocalRoadBlock,
    LocalValhalla,
    LocalValhallaNoPath,
    LocalValhallaSnapFailed,
    LocalValhallaUnavailable,
)
from scripts.road_precompute.policy import MAX_LEG_METERS


class MatrixCoverageGap(RuntimeError):
    """A blank matrix cell cannot establish that a fuel-range road leg is absent."""


def _retry_local_road_call[RoadResult](
    call: Callable[[], RoadResult],
    *,
    attempts: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> RoadResult:
    """Retry transient local service failures without retrying a known no-path."""
    if attempts < 1:
        raise ValueError("retry attempts must be positive")
    for attempt in range(attempts):
        try:
            return call()
        except LocalValhallaNoPath:
            raise
        except LocalValhallaUnavailable:
            if attempt + 1 == attempts:
                raise
            sleep(2**attempt)
    raise AssertionError("retry loop must return or raise")


def _locate_points(
    connection: sqlite3.Connection,
    points: tuple[Coordinates, ...],
    router: LocalValhalla,
    batch_size: int,
    *,
    active_only: bool = False,
) -> None:
    active_clause = "AND active = 1" if active_only else ""
    pending = [
        point_id
        for (point_id,) in connection.execute(
            "SELECT point_id FROM point "
            f"WHERE snap_meters IS NULL AND snap_error IS NULL {active_clause}"
        )
    ]
    for offset in range(0, len(pending), batch_size):
        ids = pending[offset : offset + batch_size]
        chosen = tuple(points[index] for index in ids)
        try:
            snaps = router.locate(chosen, RequestBudget(deadline_seconds=90))
            results: list[tuple[float | None, str | None, int]] = [
                (float(snap), None, index)
                for index, snap in zip(ids, snaps, strict=True)
            ]
        except LocalValhallaSnapFailed:
            results = []
            for index in ids:
                try:
                    snap = router.locate(
                        (points[index],), RequestBudget(deadline_seconds=90)
                    )[0]
                    results.append((float(snap), None, index))
                except LocalValhallaSnapFailed:
                    results.append((None, "no drivable road within snap limit", index))
        with connection:
            connection.executemany(
                "UPDATE point SET snap_meters=?, snap_error=? WHERE point_id=?",
                results,
            )
        if offset % (batch_size * 20) == 0:
            print(f"located {offset + len(ids)}/{len(pending)} points", flush=True)


def _checked_block_edges(
    sources: list[int],
    target_ids: list[int],
    by_source: dict[int, set[int]],
    block: LocalRoadBlock,
    repair: Callable[[int, int], tuple[Decimal, Decimal] | None] | None = None,
    repair_executor: Executor | None = None,
) -> list[tuple[int, int, float, float]]:
    stored: list[tuple[int, int, float, float]] = []
    missing: list[tuple[int, int]] = []
    for row_index, source in enumerate(sources):
        for column, target in enumerate(target_ids):
            if source == target or target not in by_source[source]:
                continue
            distance = block.distances_meters[row_index][column]
            duration = block.durations_seconds[row_index][column]
            if distance is None or duration is None:
                missing.append((source, target))
            elif distance <= Decimal(str(MAX_LEG_METERS)):
                stored.append((source, target, float(distance), float(duration)))
    if missing:
        if repair is None:
            raise MatrixCoverageGap(
                f"{len(missing)} matrix legs are unresolved; first source/target "
                f"IDs are {missing[0]}. Verify these with complete routing before "
                "marking a station source complete."
            )
        repairs = (
            map(lambda pair: repair(*pair), missing)
            if repair_executor is None
            else repair_executor.map(lambda pair: repair(*pair), missing)
        )
        for (source, target), repaired in zip(missing, repairs, strict=True):
            if repaired is not None and repaired[0] <= Decimal(str(MAX_LEG_METERS)):
                stored.append((source, target, float(repaired[0]), float(repaired[1])))
    return stored
