"""Copy a live v1 station-road checkpoint to an update-safe shadow database.

SQLite backup gives a consistent source snapshot while the old builder runs.
Never modify or delete the source database. The v2 builder can later resume the
copied checkpoints and append newly added station coordinates.
"""

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.domain.routing import Coordinates
from app.planning.road_cache_identity import SCHEMA_VERSION, point_set_hash

ROOT = Path(__file__).resolve().parents[1]


def migrate(source: Path, target: Path) -> dict[str, int | str]:
    source = source.resolve()
    target = target.resolve()
    if not source.is_file() or target.exists():
        raise ValueError("source must exist and shadow target must not exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as old:
        metadata = dict(old.execute("SELECT key, value FROM metadata"))
        if metadata.get("schema") != "csv-station-edges-v1-auto":
            raise ValueError("source is not a v1 station-road checkpoint")
        with closing(sqlite3.connect(target)) as shadow:
            old.backup(shadow)
    try:
        with closing(sqlite3.connect(target)) as shadow, shadow:
            if shadow.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError("shadow SQLite backup failed integrity check")
            shadow.execute(
                "ALTER TABLE point ADD COLUMN active INTEGER NOT NULL DEFAULT 1"
            )
            shadow.execute(
                "CREATE UNIQUE INDEX point_coordinates_unique "
                "ON point(latitude, longitude)"
            )
            points = tuple(
                Coordinates(latitude, longitude)
                for latitude, longitude in shadow.execute(
                    "SELECT latitude, longitude FROM point"
                )
            )
            expected = {
                "schema": SCHEMA_VERSION,
                "road_graph_version": metadata["road_graph_version"],
                "profile": metadata["profile"],
                "max_leg_meters": metadata["max_leg_meters"],
                "active_point_hash": point_set_hash(points),
            }
            shadow.execute("DELETE FROM metadata")
            shadow.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)", expected.items()
            )
            summary: dict[str, int | str] = {
                "schema": SCHEMA_VERSION,
                "points": len(points),
                "completed_sources": shadow.execute(
                    "SELECT COUNT(*) FROM complete_source"
                ).fetchone()[0],
                "directed_edges": shadow.execute(
                    "SELECT COUNT(*) FROM edge"
                ).fetchone()[0],
                "active_point_hash": expected["active_point_hash"],
            }
            if shadow.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError("migrated shadow failed SQLite integrity check")
        return summary
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=ROOT / "var/road_nationwide/station_edges.db"
    )
    parser.add_argument(
        "--target", type=Path, default=ROOT / "var/road_nationwide/station_edges_v2.db"
    )
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.target), sort_keys=True))


if __name__ == "__main__":
    main()
