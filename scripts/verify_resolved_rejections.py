"""Verify resolved low-fuel cases through actual HTTP using the shared call ledger."""

import argparse
import json
import os
import threading
from pathlib import Path
from time import perf_counter

import httpx

from scripts.atomic_checkpoint import write_checkpoint
from scripts.benchmark_budget import BenchmarkBudget
from scripts.local_common import (
    ROOT,
    load_local_env,
    planner_hash,
    sha256,
    verify_installed,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--call-ledger", required=True, type=Path)
    parser.add_argument("--call-cap", type=int, default=1200)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output exists")
    load_local_env()
    manifest = verify_installed()
    evidence = json.loads(args.evidence.read_text())
    assert evidence["candidate_sha256"] == manifest["dataset"]["candidate_sha256"]
    descriptor = json.loads((ROOT / "local-release.json").read_text())
    for key, value in descriptor["hosted_settings"].items():
        os.environ[key] = value
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django

    django.setup()
    from django.core.wsgi import get_wsgi_application
    from waitress import wasyncore
    from waitress.server import create_server

    from app.api.deadline import shutdown_planning

    channels = {}
    server = create_server(
        get_wsgi_application(), host="127.0.0.1", port=0, threads=6, map=channels
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    report = {
        "release": manifest["version"],
        "planner_sha256": planner_hash(ROOT),
        "asset_sha256": descriptor["sha256"],
        "evidence_sha256": sha256(args.evidence),
        "method": "real Waitress HTTP; first request cold, second request warm",
        "rows": [],
    }
    try:
        with BenchmarkBudget(args.call_ledger, limit=args.call_cap) as pacing:
            before = pacing.data["attempts"]
            with httpx.Client(
                base_url=f"http://127.0.0.1:{server.effective_port}",
                timeout=20,
                trust_env=False,
            ) as client:
                for result in evidence["rows"]:
                    assert result["status"] == "checked_infeasible"
                    case = result["input"]
                    body = {
                        key: case[key]
                        for key in (
                            "start",
                            "finish",
                            "initial_fuel_gallons",
                            "reserve_gallons",
                        )
                    }
                    body.update(
                        initial_fuel_price_per_gallon="3.20",
                        max_extra_duration_minutes="30",
                    )
                    for trial in (1, 2):
                        pacing.before_request()
                        began = perf_counter()
                        response = client.post("/api/v1/trips", json=body)
                        seconds = perf_counter() - began
                        error = response.json().get("error", {}).get("code")
                        report["rows"].append(
                            {
                                "case": result["case"],
                                "trial": trial,
                                "seconds": round(seconds, 4),
                                "http_status": response.status_code,
                                "error": error,
                                "correct_rejection": response.status_code == 422
                                and error == "NO_FEASIBLE_PLAN_IN_SEARCH_SCOPE",
                            }
                        )
                        report["hosted_attempts"] = pacing.data["attempts"] - before
                        write_checkpoint(args.output, report)
    finally:
        shutdown_planning()
        server.task_dispatcher.shutdown(timeout=5)
        wasyncore.close_all(map=channels)
        thread.join(timeout=2)
    report["passed"] = len(report["rows"]) == 4 and all(
        row["correct_rejection"] and row["seconds"] < 10 for row in report["rows"]
    )
    write_checkpoint(args.output, report)
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
