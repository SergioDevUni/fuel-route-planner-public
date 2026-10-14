"""Freeze tiny synthetic smoke assets in isolation; never a route-quality evidence."""

import argparse
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.local_common import ROOT


def prepare(output: Path):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections
    from django.test import override_settings

    from scripts.package_local_assets import package_assets
    from tests.support.readiness import install_ready_assets

    if output.exists():
        raise ValueError("Choose a fresh fixture directory")
    with TemporaryDirectory(prefix="fuel-container-fixture-") as raw:
        root = Path(raw)
        for name in ("app", "config"):
            shutil.copytree(
                ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__")
            )
        for name in ("pyproject.toml", "uv.lock"):
            shutil.copy2(ROOT / name, root / name)
        (root / "var").mkdir()
        database = root / "var/app.sqlite3"
        # This CLI exclusively owns its disposable DB; never touch the installed DB.
        connections.close_all()
        previous = connections["default"].settings_dict["NAME"]
        with override_settings(BASE_DIR=root):
            settings.DATABASES["default"]["NAME"] = database
            connections["default"].settings_dict["NAME"] = database
            try:
                call_command("migrate", verbosity=0)
                install_ready_assets(root)
                road = root / "var/road_nationwide/station_edges.db"
                road.parent.mkdir()
                with closing(sqlite3.connect(road)) as db, db:
                    db.executescript(
                        "CREATE TABLE metadata(key,value); "
                        "CREATE TABLE point(point_id); "
                        "CREATE TABLE complete_source(point_id); "
                        "INSERT INTO point VALUES(1); "
                        "INSERT INTO complete_source VALUES(1); "
                        "INSERT INTO metadata "
                        "VALUES('road_graph_version','synthetic-fixture');"
                    )
                output.mkdir(parents=True)
                return package_assets(
                    root,
                    output / "assets.zip",
                    "synthetic-container-smoke",
                    descriptor_path=output / "local-release.json",
                )
            finally:
                connections.close_all()
                connections["default"].settings_dict["NAME"] = previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.output)
    print(f"Synthetic smoke assets prepared: {args.output}")


if __name__ == "__main__":
    main()
