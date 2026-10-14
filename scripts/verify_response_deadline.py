"""Measure actual Waitress HTTP under isolated SQLite contention, without routing."""

import argparse
import json
import os
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import httpx

from scripts.local_common import ROOT, planner_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--planning-seconds", type=float, default=13)
    parser.add_argument("--checkpoint-after", type=float)
    args = parser.parse_args()
    if (
        args.checkpoint_after is not None
        and not 0 <= args.checkpoint_after < args.planning_seconds
    ):
        parser.error("checkpoint must precede planning completion")
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django

    django.setup()
    from django.conf import settings
    from django.core.wsgi import get_wsgi_application
    from django.db import connection
    from waitress import wasyncore
    from waitress.server import create_server

    from app.api.deadline import shutdown_planning
    from app.api.models import TripResult

    rows = []
    with tempfile.TemporaryDirectory(prefix="fuel-deadline-") as folder:
        connection.close()
        connection.settings_dict["NAME"] = str(Path(folder) / "test.sqlite3")
        settings.DATABASES["default"]["NAME"] = connection.settings_dict["NAME"]
        # HTTP workers must release the disposable database before Windows cleanup.
        connection.settings_dict["CONN_MAX_AGE"] = 0
        settings.DATABASES["default"]["CONN_MAX_AGE"] = 0
        with connection.schema_editor() as editor:
            editor.create_model(TripResult)
        channels = {}
        server = create_server(
            get_wsgi_application(), host="127.0.0.1", port=0, threads=12, map=channels
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        def calculate(trip, budget):
            payload = {"status": "verified", "assumptions": {}}
            began = time.monotonic()
            if args.checkpoint_after is not None:
                time.sleep(args.checkpoint_after)
                budget.publish_verified_payload(
                    payload, (Decimal(0), Decimal(0), 0, ())
                )
            while time.monotonic() - began < args.planning_seconds:
                budget.check_deadline()
                time.sleep(0.01)
            return payload

        def request(_):
            with httpx.Client(timeout=20, trust_env=False) as client:
                start = time.monotonic()
                response = client.post(
                    f"http://127.0.0.1:{server.effective_port}/api/v1/trips",
                    json={
                        "start": {
                            "type": "coordinates",
                            "latitude": 40,
                            "longitude": -90,
                        },
                        "finish": {
                            "type": "coordinates",
                            "latitude": 40,
                            "longitude": -70,
                        },
                    },
                )
                row = {
                    "seconds": round(time.monotonic() - start, 4),
                    "status": response.status_code,
                    "error": response.json().get("error", {}).get("code"),
                }
                if response.status_code == 200:
                    payload = response.json()
                    saved = client.get(
                        f"http://127.0.0.1:{server.effective_port}/api/v1/trips/{payload['trip_id']}",
                        headers={"Authorization": "Bearer " + payload["result_token"]},
                    )
                    row["saved_retrievable"] = saved.status_code == 200
                return row

        try:
            with (
                patch("app.api.views.calculate", calculate),
                patch("app.api.views.calculation_keys", return_value=None),
            ):
                for users, locked in ((1, True), (6, True), (6, False)):
                    holder = sqlite3.connect(connection.settings_dict["NAME"])
                    if locked:
                        holder.execute("BEGIN IMMEDIATE")
                    try:
                        with ThreadPoolExecutor(max_workers=users) as pool:
                            observations = list(pool.map(request, range(users)))
                        rows.append(
                            {
                                "users": users,
                                "writer_locked": locked,
                                "rows": observations,
                            }
                        )
                    finally:
                        holder.rollback()
                        holder.close()
        finally:
            shutdown_planning()
            server.task_dispatcher.shutdown(timeout=5)
            wasyncore.close_all(map=channels)
            thread.join(timeout=2)
            connection.close()
    passed = all(
        row["seconds"] <= (10 if args.checkpoint_after is not None else 15)
        and row["status"] == (503 if batch["writer_locked"] else 200)
        and (batch["writer_locked"] or row.get("saved_retrievable"))
        for batch in rows
        for row in batch["rows"]
    )
    result = {
        "planner_sha256": planner_hash(ROOT),
        "method": "real Waitress HTTP, synthetic planning, disposable SQLite",
        "planning_seconds": args.planning_seconds,
        "checkpoint_after_seconds": args.checkpoint_after,
        "response_limit_seconds": 10 if args.checkpoint_after is not None else 15,
        "hosted_calls": 0,
        "passed": passed,
        "batches": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
