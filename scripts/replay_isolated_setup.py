"""Replay core imports and station publication in a disposable local database.

This uses the already prepared, source-hashed place and station evidence. It
does not download data, rebuild the nationwide road graph, or mutate the live
database and candidate snapshot.
"""

import json
import os
import tempfile
from io import StringIO
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.conf import settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="fuel-planner-replay-") as temporary:
        folder = Path(temporary)
        settings.DATABASES["default"]["NAME"] = folder / "app.sqlite3"

        import django  # noqa: PLC0415

        django.setup()
        from django.core.management import call_command  # noqa: PLC0415
        from django.db import connections  # noqa: PLC0415

        try:
            quiet = StringIO()
            call_command("migrate", verbosity=0, stdout=quiet)
            call_command(
                "import_prices",
                ROOT / "data/raw/fuel-prices-for-be-assessment.csv",
                verbosity=0,
                stdout=quiet,
            )
            call_command(
                "import_places",
                ROOT / "data/prepared/locations",
                verbosity=0,
                stdout=quiet,
            )
            output = folder / "station_candidates"
            call_command(
                "build_station_snapshot", output_dir=output, verbosity=0, stdout=quiet
            )

            live = json.loads(
                (ROOT / "var/station_candidates/manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            replay = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            if replay != live:
                raise RuntimeError(
                    "isolated station snapshot differs from live manifest"
                )
            if (output / "candidates.json").read_bytes() != (
                ROOT / "var/station_candidates/candidates.json"
            ).read_bytes():
                raise RuntimeError(
                    "isolated station snapshot differs from live candidates"
                )
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "snapshot_offer_count": replay["candidate_count"],
                        "linked_csv_station_ids": replay["linked_csv_station_ids"],
                        "snapshot_version": replay["candidate_sha256"],
                        "database": "disposable",
                        "live_files_modified": False,
                    },
                    sort_keys=True,
                )
            )
        finally:
            connections.close_all()


if __name__ == "__main__":
    main()
