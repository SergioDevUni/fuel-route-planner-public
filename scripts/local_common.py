"""Shared local-delivery checks; no implicit changes to production settings."""

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path, PurePosixPath

from app.stations.files import sha256 as sha256

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "LOCAL_ASSETS_MANIFEST.json"


def planner_hash(root: Path) -> str:
    files = [root / "pyproject.toml", root / "uv.lock"]
    files += sorted((root / "app").rglob("*.py"))
    files += sorted((root / "config").rglob("*.py"))
    digest = hashlib.sha256()
    for path in sorted(files):
        if path.name.startswith("test"):
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        # Git normalizes text on checkout; newline style is not a planner change.
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def load_local_env(root: Path = ROOT) -> None:
    """Read literal KEY=VALUE pairs; process environment has precedence."""
    path = root / ".env"
    if not path.is_file():
        return
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"Invalid .env assignment on line {number}")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Unclosed .env quote on line {number}")
            value = value[1:-1]
        os.environ.setdefault(key, value)


def asset_path(root: Path, name: str) -> Path:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or path.as_posix() != name
        or any(part in (".", "..") or ":" in part for part in path.parts)
        or "\\" in name
        or "\x00" in name
        or not (
            name.startswith(
                ("data/", "var/station_candidates/", "var/road_nationwide/")
            )
            or name == "var/app.sqlite3"
        )
    ):
        raise ValueError("Asset archive contains an unsupported path")
    target = (root / name).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Asset path leaves the installation directory")
    return target


def verify_installed(root: Path = ROOT) -> dict:
    path = root / "var" / MANIFEST
    if not path.is_file():
        raise ValueError("Prepared data is missing. Run scripts.setup_local first.")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest["planner_sha256"] != planner_hash(root):
        raise ValueError("Code/dependencies differ from the installed release baseline")
    for name, record in manifest["contents"].items():
        if name == "var/app.sqlite3":
            continue  # Results and migrations legitimately change this database.
        target = asset_path(root, name)
        if not target.is_file() or sha256(target) != record["sha256"]:
            raise ValueError(f"Prepared asset differs from release: {name}")
    db = root / "var/app.sqlite3"
    if not db.is_file():
        raise ValueError("Local database is missing. Run scripts.setup_local first.")
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("Local database integrity check failed")
        active = connection.execute(
            "SELECT source_sha256, counts FROM app_datasetversion WHERE status='active'"
        ).fetchall()
        if len(active) != 1 or active[0][0] != manifest["dataset"]["source_sha256"]:
            raise ValueError("Local database has a different active CSV dataset")
        counts = json.loads(active[0][1])
        if counts["station_ids"] != manifest["dataset"]["station_ids"]:
            raise ValueError("Local database station identity count differs")
    return manifest
