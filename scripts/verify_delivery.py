"""Restore the handoff ZIP and verify its data, app and optional live trip."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(
    archive_path: Path, *, hosted_smoke: bool = False, fresh_env: bool = False
) -> dict[str, object]:
    sidecar = archive_path.with_suffix(archive_path.suffix + ".sha256")
    if sidecar.is_file() and sidecar.read_text(encoding="ascii").split()[0] != _hash(
        archive_path
    ):
        raise RuntimeError("delivery ZIP differs from its SHA-256 sidecar")
    with tempfile.TemporaryDirectory(prefix="delivery-restore-") as raw:
        restored = Path(raw)
        with ZipFile(archive_path) as archive:
            for name in archive.namelist():
                target = (restored / name).resolve()
                if not target.is_relative_to(restored.resolve()):
                    raise ValueError("delivery archive contains an unsafe path")
            archive.extractall(restored)
        manifest = json.loads((restored / "DELIVERY_MANIFEST.json").read_text())
        for name, record in manifest["contents"].items():
            path = restored / name
            if (
                path.stat().st_size != record["bytes"]
                or _hash(path) != record["sha256"]
            ):
                raise RuntimeError(f"delivery file differs from its manifest: {name}")
        if (restored / ".env").exists():
            raise RuntimeError("private environment file was packaged")
        road_name = manifest["road"].get("database", "station_edges.db")
        if Path(road_name).name != road_name:
            raise ValueError("unsafe road database name in delivery manifest")
        with closing(
            sqlite3.connect(restored / "var/road_nationwide" / road_name)
        ) as road:
            if road.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise RuntimeError("restored road checkpoint failed integrity check")
            points = road.execute("SELECT COUNT(*) FROM point").fetchone()[0]
            completed = road.execute("SELECT COUNT(*) FROM complete_source").fetchone()[
                0
            ]
            version = dict(road.execute("SELECT key,value FROM metadata"))[
                "road_graph_version"
            ]
        if (
            points != manifest["road"]["points"]
            or completed != manifest["road"]["completed_sources"]
            or version != manifest["road"]["graph_version"]
        ):
            raise RuntimeError("restored road checkpoint differs from its manifest")
        env = os.environ.copy()
        env["ORS_API_KEY"] = "restore-readiness-check"
        env["FUEL_PARTIAL_ROAD_REFINER"] = "0"
        env["FUEL_NATIONAL_LOCAL_ROUTER"] = "0"
        python = sys.executable
        if fresh_env:
            uv = shutil.which("uv")
            if uv is None:
                raise RuntimeError("uv is required for a fresh dependency replay")
            installed = subprocess.run(
                [uv, "sync", "--locked", "--no-dev"],
                cwd=restored,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            if installed.returncode != 0:
                raise RuntimeError(
                    f"restored dependency installation failed: {installed.stderr}"
                )
            python_path = (
                restored / ".venv/Scripts/python.exe"
                if os.name == "nt"
                else restored / ".venv/bin/python"
            )
            python = str(python_path)
        checked = subprocess.run(
            [python, "manage.py", "check"],
            cwd=restored,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if checked.returncode != 0:
            raise RuntimeError(f"restored Django check failed: {checked.stderr}")
        command = (
            "import os; "
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); "
            "import django; django.setup(); "
            "from django.test import Client; "
            "from app.stations.models import DatasetVersion; "
            "from app.planning.candidates import CandidateIndex; "
            "from pathlib import Path; "
            "d=DatasetVersion.objects.get(status='active'); "
            "i=CandidateIndex(Path('var/station_candidates'),d.source_sha256); "
            "assert len(i.stations)==6626; "
            "assert d.counts['station_ids']==6738; "
            "assert Client().get('/health/live').status_code==200; "
            "assert Client().get('/health/ready').status_code==200"
        )
        checked = subprocess.run(
            [python, "-c", command],
            cwd=restored,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if checked.returncode != 0:
            raise RuntimeError(f"restored data/health check failed: {checked.stderr}")
        if hosted_smoke:
            from scripts.check_pilot_routes import routing_key

            env["ORS_API_KEY"] = routing_key()
            checked = subprocess.run(
                [python, "-m", "scripts.smoke_trip", "--station-case"],
                cwd=restored,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            if checked.returncode != 0:
                raise RuntimeError(f"restored hosted trip failed: {checked.stderr}")
        return {
            "status": "passed",
            "files": len(manifest["contents"]),
            "linked_csv_ids": 6738,
            "us_offers": 6626,
            "hosted_smoke": hosted_smoke,
            "fresh_environment": fresh_env,
            "road_completed_sources": manifest["road"]["completed_sources"],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archive",
        nargs="?",
        type=Path,
        default=ROOT / "var/delivery/fuel-route-planner-2026-10-10.zip",
    )
    parser.add_argument("--hosted-smoke", action="store_true")
    parser.add_argument("--fresh-env", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            verify(
                args.archive,
                hosted_smoke=args.hosted_smoke,
                fresh_env=args.fresh_env,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
