"""Create a credential-free, restorable local assessment handoff ZIP."""

import argparse
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = (".github", "app", "config", "data", "docs", "scripts", "tests")
SOURCE_FILES = (
    ".env.example",
    ".gitignore",
    "README.md",
    "manage.py",
    "pyproject.toml",
    "uv.lock",
)
RUNTIME_DIRS = (
    "var/station_candidates",
    "var/static_root",
    "var/road_nationwide/endpoint_blocks",
)


def _files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )


def _backup_database(source: Path, target: Path) -> None:
    with (
        closing(sqlite3.connect(source)) as original,
        closing(sqlite3.connect(target)) as copy,
    ):
        original.backup(copy)
        if copy.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError(f"database backup failed integrity check: {source}")


def _record(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "bytes": path.stat().st_size}


def package(output: Path) -> dict[str, object]:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="delivery-build-", dir=output.parent
    ) as raw:
        temporary = Path(raw)
        app_database = temporary / "app.sqlite3"
        road_source = ROOT / "var/road_nationwide/station_edges_v2_live.db"
        if not road_source.is_file():
            road_source = ROOT / "var/road_nationwide/station_edges.db"
        road_name = road_source.name
        road_database = temporary / road_name
        _backup_database(ROOT / "var/app.sqlite3", app_database)
        with closing(sqlite3.connect(app_database)) as connection:
            connection.execute("DELETE FROM app_tripresult")
            connection.commit()
            connection.execute("VACUUM")
        _backup_database(road_source, road_database)
        with closing(sqlite3.connect(road_database)) as connection:
            road_points = connection.execute("SELECT COUNT(*) FROM point").fetchone()[0]
            road_metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            completed = connection.execute(
                "SELECT COUNT(*) FROM complete_source"
            ).fetchone()[0]
            road_version = road_metadata["road_graph_version"]
            active_points = (
                connection.execute(
                    "SELECT COUNT(*) FROM point WHERE active = 1"
                ).fetchone()[0]
                if road_metadata.get("schema") == "csv-station-edges-v2-auto"
                else road_points
            )
            active_completed = (
                connection.execute(
                    "SELECT COUNT(*) FROM complete_source "
                    "JOIN point USING(point_id) WHERE point.active = 1"
                ).fetchone()[0]
                if road_metadata.get("schema") == "csv-station-edges-v2-auto"
                else completed
            )
        files = {
            path.relative_to(ROOT).as_posix(): path
            for directory in SOURCE_DIRS + RUNTIME_DIRS
            for path in _files(ROOT / directory)
        }
        files.update({name: ROOT / name for name in SOURCE_FILES})
        files["var/app.sqlite3"] = app_database
        files[f"var/road_nationwide/{road_name}"] = road_database
        priority = ROOT / "var/road_nationwide/priority_albuquerque_denver.json"
        if priority.is_file():
            files["var/road_nationwide/priority_albuquerque_denver.json"] = priority
        seed = ROOT / "var/road_nationwide/priority_albuquerque_denver_seed.json"
        if seed.is_file():
            files["var/road_nationwide/priority_albuquerque_denver_seed.json"] = seed
        marker = road_source.with_suffix(".complete.json")
        if marker.is_file() and active_completed == active_points:
            files[f"var/road_nationwide/{marker.name}"] = marker
        manifest: dict[str, object] = {
            "created_utc": datetime.now(UTC).isoformat(),
            "contents": {name: _record(path) for name, path in sorted(files.items())},
            "road": {
                "graph_version": road_version,
                "database": road_name,
                "points": road_points,
                "active_points": active_points,
                "completed_sources": completed,
                "active_completed_sources": active_completed,
                "complete": marker.is_file() and active_completed == active_points,
                "valhalla_tiles_included": False,
            },
            "private_credentials_included": False,
        }
        with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=6) as archive:
            for name, path in sorted(files.items()):
                archive.write(path, name)
            archive.writestr(
                "DELIVERY_MANIFEST.json", json.dumps(manifest, indent=2, sort_keys=True)
            )
        with ZipFile(output) as archive:
            if archive.testzip() is not None:
                raise RuntimeError("delivery ZIP failed its CRC check")
    zip_hash = _record(output)["sha256"]
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{zip_hash}  {output.name}\n", encoding="ascii"
    )
    return {
        "output": str(output),
        "bytes": output.stat().st_size,
        "files": len(files) + 1,
        "road_completed_sources": completed,
        "road_points": road_points,
        "sha256": zip_hash,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "var/delivery/fuel-route-planner-2026-10-10.zip",
    )
    args = parser.parse_args()
    print(json.dumps(package(args.output), sort_keys=True))


if __name__ == "__main__":
    main()
