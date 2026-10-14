"""Force a preferred verified return and verify six-user repeats use its cache."""

import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx

from scripts.atomic_checkpoint import write_checkpoint
from scripts.benchmark_budget import BenchmarkBudget
from scripts.local_common import ROOT, load_local_env, planner_hash, verify_installed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--call-ledger", required=True, type=Path)
    parser.add_argument("--call-cap", type=int, default=1300)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output exists")
    load_local_env()
    manifest = verify_installed()
    descriptor = json.loads((ROOT / "local-release.json").read_text())
    for key, value in descriptor["hosted_settings"].items():
        os.environ[key] = value
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django

    django.setup()
    from django.core.wsgi import get_wsgi_application
    from waitress import wasyncore
    from waitress.server import create_server

    from app.api import service
    from app.api.assets import load_candidates
    from app.api.deadline import shutdown_planning
    from scripts.benchmarking.contracts import validate_verified_trip

    index = load_candidates(
        descriptor["dataset"]["source_sha256"],
        descriptor["dataset"]["candidate_sha256"],
    )
    ids = {s.station_id for s in index.stations}
    original = service.timed_calculate_uncached

    def delayed(trip, candidate_hash, start, finish, budget):
        result = original(trip, candidate_hash, start, finish, budget)
        # Delay only optional completion after a real verified road plan exists.
        # The result-cache owner cannot finish before the HTTP decision cancels it.
        while not budget.cancelled:
            time.sleep(0.01)
        budget.check_deadline()
        return result

    channels = {}
    server = create_server(
        get_wsgi_application(), host="127.0.0.1", port=0, threads=12, map=channels
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    report = {
        "release": manifest["version"],
        "planner_sha256": planner_hash(ROOT),
        "asset_sha256": descriptor["sha256"],
        "method": "live ORS plan; optional completion deliberately delayed; "
        "real Waitress HTTP and saved retrieval",
        "rows": [],
    }
    body = {
        "start": {"type": "city", "city": "Seattle", "state": "WA"},
        "finish": {"type": "city", "city": "Denver", "state": "CO"},
        "initial_fuel_gallons": "50",
        "reserve_gallons": "0",
        "initial_fuel_price_per_gallon": "3.20",
        "max_extra_duration_minutes": "30",
    }

    def request(_):
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.effective_port}",
            timeout=20,
            trust_env=False,
        ) as client:
            began = time.perf_counter()
            response = client.post("/api/v1/trips", json=body)
            seconds = time.perf_counter() - began
            payload = response.json()
            assert response.status_code == 200
            assert not validate_verified_trip(payload, ids)
            saved = client.get(
                "/api/v1/trips/" + payload["trip_id"],
                headers={"Authorization": "Bearer " + payload["result_token"]},
            )
            assert (
                saved.status_code == 200 and saved.json()["route"] == payload["route"]
            )
            return {
                "seconds": round(seconds, 4),
                "saved_matches": True,
                "purchase_cost": payload["costs"]["fuel_purchases_cost"],
                "interrupted": payload["assumptions"].get(
                    "optimization_interrupted", False
                ),
            }

    try:
        with BenchmarkBudget(args.call_ledger, limit=args.call_cap) as pacing:
            before = pacing.data["attempts"]
            with patch("app.api.service.timed_calculate_uncached", delayed):
                pacing.before_request()
                cold = request(0)
                report["forced_preferred_return"] = cold
                assert cold["interrupted"] and cold["seconds"] < 10
                after_cold = pacing.data["attempts"]
                for wave in (1, 2):
                    pacing.before_request()
                    with ThreadPoolExecutor(max_workers=6) as pool:
                        rows = list(pool.map(request, range(6)))
                    report["rows"].extend(dict(row, wave=wave) for row in rows)
                report["cached_hosted_attempts"] = pacing.data["attempts"] - after_cold
                report["hosted_attempts"] = pacing.data["attempts"] - before
    finally:
        shutdown_planning()
        server.task_dispatcher.shutdown(timeout=5)
        wasyncore.close_all(map=channels)
        thread.join(timeout=2)
    report["passed"] = report["cached_hosted_attempts"] == 0 and all(
        row["seconds"] < 2 and row["purchase_cost"] == cold["purchase_cost"]
        for row in report["rows"]
    )
    write_checkpoint(args.output, report)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "hosted_attempts": report["hosted_attempts"],
                "cached_hosted_attempts": report["cached_hosted_attempts"],
            },
            indent=2,
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
