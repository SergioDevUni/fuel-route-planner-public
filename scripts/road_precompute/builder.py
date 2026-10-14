import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Lock

from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.planning.road_cache_identity import SCHEMA_VERSION as UPDATE_SAFE_SCHEMA
from app.planning.road_cache_identity import point_set_hash
from app.routing.local_valhalla import (
    LocalRoadBlock,
    LocalValhalla,
    LocalValhallaNoPath,
)
from scripts.road_precompute.acquisition import (
    _checked_block_edges,
    _locate_points,
    _retry_local_road_call,
)
from scripts.road_precompute.policy import (
    TARGET_GROUP_CELL_DEGREES,
    TARGET_SCREEN_POLICY,
)
from scripts.road_precompute.scheduling import (
    _points,
    _priority_groups,
    _read_priority_file,
    _source_targets,
    _spatial_source_groups,
)
from scripts.road_precompute.storage import (
    _connect,
    _initialize,
    _reconcile_update_safe_points,
)


def build(
    candidates: Path,
    database: Path,
    url: str,
    graph_version: str,
    *,
    batch_size: int,
    workers: int,
    max_groups: int | None,
    priority_file: Path | None = None,
    priority_only: bool = False,
) -> dict[str, int | str]:
    if (
        not 1 <= batch_size <= 20
        or not 1 <= workers <= 12
        or (max_groups is not None and max_groups < 1)
    ):
        raise ValueError("invalid batch size, worker count, or group limit")
    if priority_only and priority_file is None:
        raise ValueError("priority-only build requires a priority file")
    points, source_hash, candidate_hash, offer_count = _points(candidates)
    current_points = points
    connection = _connect(database)
    router = LocalValhalla(
        url,
        graph_version,
        max_points=batch_size,
        max_snap_meters=Decimal(1000),
        request_timeout_seconds=60,
    )
    repair_executor = ThreadPoolExecutor(max_workers=workers)
    repair_lock = Lock()
    try:
        schema = dict(connection.execute("SELECT key, value FROM metadata")).get(
            "schema"
        )
        update_safe = schema == UPDATE_SAFE_SCHEMA
        if update_safe:
            previous_point_hash = dict(
                connection.execute("SELECT key, value FROM metadata")
            ).get("active_point_hash")
            points = _reconcile_update_safe_points(connection, points, graph_version)
            if previous_point_hash != point_set_hash(current_points):
                database.with_suffix(".complete.json").unlink(missing_ok=True)
        else:
            _initialize(connection, points, source_hash, candidate_hash, graph_version)
        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        target_policy = metadata.get("target_screen_policy")
        if target_policy is None:
            prior_sources = connection.execute(
                "SELECT COUNT(*) FROM complete_source"
            ).fetchone()[0]
            target_policy = (
                "legacy_raw_sources_plus_snap_relaxed_updates"
                if prior_sources
                else TARGET_SCREEN_POLICY
            )
            with connection:
                connection.execute(
                    "INSERT INTO metadata VALUES ('target_screen_policy',?)",
                    (target_policy,),
                )
        priority_ids = _read_priority_file(
            priority_file,
            candidate_hash,
            graph_version,
            len(points),
            stable_points=update_safe,
        )
        _locate_points(connection, points, router, batch_size, active_only=update_safe)
        active_clause = "AND active = 1" if update_safe else ""
        valid = [
            point_id
            for (point_id,) in connection.execute(
                "SELECT point_id FROM point WHERE snap_meters IS NOT NULL "
                f"{active_clause}"
            )
        ]
        complete = {
            point_id
            for (point_id,) in connection.execute(
                "SELECT point_id FROM complete_source"
            )
        }
        remaining = [point_id for point_id in valid if point_id not in complete]
        source_groups, priority_group_count = _priority_groups(
            remaining, points, batch_size, priority_ids
        )
        if priority_only:
            source_groups = source_groups[:priority_group_count]
        print(
            f"priority sources remaining: "
            f"{len(priority_ids.intersection(remaining))}/{len(priority_ids)}",
            flush=True,
        )
        groups = 0
        repaired_legs = 0

        def repair(source: int, target: int) -> tuple[Decimal, Decimal] | None:
            nonlocal repaired_legs
            try:
                leg = _retry_local_road_call(
                    lambda: router.road_leg(
                        points[source],
                        points[target],
                        RequestBudget(deadline_seconds=90),
                    )
                )
            except LocalValhallaNoPath:
                return None
            with repair_lock:
                repaired_legs += 1
            return leg.distance_meters, leg.duration_seconds

        for sources in source_groups:
            by_source, targets = _source_targets(sources, valid, points)
            target_groups = _spatial_source_groups(
                targets, points, batch_size, cell_degrees=TARGET_GROUP_CELL_DEGREES
            )
            source_points = tuple(points[index] for index in sources)

            def fetch_block(
                target_ids: list[int],
                source_points: tuple[Coordinates, ...] = source_points,
            ) -> tuple[list[int], LocalRoadBlock]:
                block = _retry_local_road_call(
                    lambda: router.matrix_block(
                        source_points,
                        tuple(points[index] for index in target_ids),
                        RequestBudget(deadline_seconds=90),
                    )
                )
                return target_ids, block

            blocks = list(repair_executor.map(fetch_block, target_groups))
            stored_count = 0
            for row_index, source in enumerate(sources):
                stored: list[tuple[int, int, float, float]] = []
                for target_ids, block in blocks:
                    source_block = LocalRoadBlock(
                        (block.distances_meters[row_index],),
                        (block.durations_seconds[row_index],),
                    )
                    stored.extend(
                        _checked_block_edges(
                            [source],
                            target_ids,
                            by_source,
                            source_block,
                            repair,
                            repair_executor,
                        )
                    )
                with connection:
                    connection.executemany(
                        "INSERT OR REPLACE INTO edge VALUES (?, ?, ?, ?)", stored
                    )
                    connection.execute(
                        "INSERT INTO complete_source VALUES (?)", (source,)
                    )
                stored_count += len(stored)
                print(
                    f"completed source {source}: {len(stored)} drivable edges, "
                    f"{repaired_legs} direct-route repairs so far",
                    flush=True,
                )
            groups += 1
            print(
                f"completed group {groups}: {len(sources)} sources, "
                f"{len(targets)} nearby targets, {stored_count} drivable edges, "
                f"{repaired_legs} direct-route repairs so far",
                flush=True,
            )
            if groups == priority_group_count and priority_group_count:
                print("priority source groups complete", flush=True)
            if max_groups is not None and groups >= max_groups:
                break
        completed_count = connection.execute(
            "SELECT COUNT(*) FROM complete_source "
            "JOIN point USING(point_id) WHERE point.active = 1"
            if update_safe
            else "SELECT COUNT(*) FROM complete_source"
        ).fetchone()[0]
        summary: dict[str, int | str] = {
            "csv_offers": offer_count,
            "unique_points": len(current_points) if update_safe else len(points),
            "road_located_points": len(valid),
            "completed_sources": completed_count,
            "stored_directed_legs": connection.execute(
                "SELECT COUNT(*) FROM edge"
            ).fetchone()[0],
            "direct_route_repairs": repaired_legs,
            "graph_version": graph_version,
            "target_screen_policy": target_policy,
        }
        if summary["completed_sources"] == len(valid):
            marker = database.with_suffix(".complete.json")
            identity = (
                {
                    "schema": UPDATE_SAFE_SCHEMA,
                    "active_point_hash": point_set_hash(current_points),
                }
                if update_safe
                else {"candidate_version": candidate_hash, "source_hash": source_hash}
            )
            marker.write_text(
                json.dumps(
                    {
                        **summary,
                        **identity,
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        return summary
    finally:
        repair_executor.shutdown(wait=True, cancel_futures=True)
        router.close()
        connection.close()
