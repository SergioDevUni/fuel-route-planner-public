"""Freeze prepared inputs and sanitized databases for a GitHub data release."""

import argparse
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from scripts.backup_runtime_database import backup_database
from scripts.local_common import MANIFEST, ROOT, planner_hash, sha256

HOSTED_SETTINGS = {
    "FUEL_PLANNING_CAPACITY": "6",
    "FUEL_MATRIX_GRAPH": "1",
    "FUEL_MATRIX_FIRST": "1",
    "FUEL_MATRIX_CACHE_VERSION": "v1",
    "FUEL_PARTIAL_ROAD_REFINER": "0",
    "FUEL_PARTIAL_ROAD_LOCAL_FIRST": "0",
    "FUEL_PARTIAL_ROAD_PORTFOLIO": "0",
    "FUEL_NATIONAL_LOCAL_ROUTER": "0",
}


def package_assets(
    root: Path, output: Path, version: str, *, descriptor_path: Path | None = None
) -> dict:
    if output.exists():
        raise ValueError("Release output must be a new file")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="local-assets-") as raw:
        temporary = Path(raw)
        seed = temporary / "app.sqlite3"
        backup_database(root / "var/app.sqlite3", seed)
        with closing(sqlite3.connect(seed)) as connection:
            connection.execute("DELETE FROM app_tripresult")
            connection.commit()
            connection.execute("VACUUM")
            active = connection.execute(
                "SELECT source_sha256, counts FROM app_datasetversion "
                "WHERE status='active'"
            ).fetchall()
            if len(active) != 1:
                raise ValueError("Exactly one active dataset is required")
            counts = json.loads(active[0][1])
        road_source = root / "var/road_nationwide/station_edges_v2_live.db"
        if not road_source.is_file():
            road_source = root / "var/road_nationwide/station_edges.db"
        road = temporary / road_source.name
        backup_database(road_source, road)
        with closing(sqlite3.connect(road)) as connection:
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            road_info = {
                "database": road.name,
                "graph_version": metadata["road_graph_version"],
                "points": connection.execute("SELECT COUNT(*) FROM point").fetchone()[
                    0
                ],
                "completed_sources": connection.execute(
                    "SELECT COUNT(*) FROM complete_source"
                ).fetchone()[0],
                "valhalla_tiles_included": False,
            }
        candidate = json.loads(
            (root / "var/station_candidates/manifest.json").read_text()
        )
        if candidate["source_sha256"] != active[0][0]:
            raise ValueError(
                "Candidate snapshot and database have different CSV versions"
            )
        if (
            sha256(root / "var/station_candidates/candidates.json")
            != candidate["candidate_sha256"]
        ):
            raise ValueError("Candidate snapshot hash does not match its manifest")
        folders = (
            "data",
            "var/station_candidates",
            "var/road_nationwide/endpoint_blocks",
        )
        files = {
            path.relative_to(root).as_posix(): path
            for folder in folders
            for path in sorted((root / folder).rglob("*"))
            if path.is_file()
            and not path.is_symlink()
            and "__pycache__" not in path.parts
        }
        files["var/app.sqlite3"] = seed
        files[f"var/road_nationwide/{road.name}"] = road
        marker = road_source.with_suffix(".complete.json")
        if marker.is_file():
            files[f"var/road_nationwide/{marker.name}"] = marker
        manifest = {
            "schema": 1,
            "version": version,
            "created_utc": datetime.now(UTC).isoformat(),
            "planner_sha256": planner_hash(root),
            "dataset": {
                "source_sha256": active[0][0],
                "station_ids": counts["station_ids"],
                "us_offers": candidate["candidate_count"],
                "candidate_sha256": candidate["candidate_sha256"],
            },
            "road": road_info,
            "hosted_settings": HOSTED_SETTINGS,
            "private_results_included": False,
            "contents": {},
        }
        with ZipFile(output, "x", ZIP_DEFLATED, compresslevel=6) as archive:
            for name, path in sorted(files.items()):
                # Freeze bytes once; the hash describes the actual ZIP contents.
                content = path.read_bytes()
                archive.writestr(name, content)
                manifest["contents"][name] = {
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "bytes": len(content),
                }
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True))
    descriptor = {
        "schema": 1,
        "repository": "SergioDevUni/fuel-route-planner-public",
        "version": version,
        "asset": output.name,
        "sha256": sha256(output),
        "bytes": output.stat().st_size,
        "planner_sha256": manifest["planner_sha256"],
        "dataset": manifest["dataset"],
        "road": manifest["road"],
        "hosted_settings": HOSTED_SETTINGS,
    }
    destination = descriptor_path or root / "local-release.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(descriptor, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(".zip.sha256").write_text(
        descriptor["sha256"] + "  " + output.name + "\n", encoding="ascii"
    )
    return descriptor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--descriptor-output",
        type=Path,
        help="Write a separate candidate descriptor; preserve the historical release",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            package_assets(
                ROOT, args.output, args.version, descriptor_path=args.descriptor_output
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
