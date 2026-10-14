"""Record real HTTP trips and saved maps from an installed local release."""

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from scripts.local_common import ROOT, planner_hash, verify_installed

CASES = {
    "saved-map": {
        "start": {"type": "city", "city": "Price", "state": "UT"},
        "finish": {"type": "city", "city": "Price", "state": "UT"},
        "initial_fuel_gallons": "50",
        "initial_fuel_price_per_gallon": "3.20",
    },
    "short-trip": {
        "start": {"type": "city", "city": "Chicago", "state": "IL"},
        "finish": {"type": "city", "city": "Joliet", "state": "IL"},
        "initial_fuel_gallons": "50",
        "initial_fuel_price_per_gallon": "3.20",
    },
    "single-stop": {
        "start": {"type": "coordinates", "latitude": 38.995, "longitude": -110.17},
        "finish": {"type": "city", "city": "Price", "state": "UT"},
        "initial_fuel_gallons": "1",
        "initial_fuel_price_per_gallon": "3.20",
    },
    "multiple-stops": {
        "start": {"type": "city", "city": "Los Angeles", "state": "CA"},
        "finish": {"type": "city", "city": "Dallas", "state": "TX"},
        "initial_fuel_gallons": "50",
        "initial_fuel_price_per_gallon": "3.20",
    },
}


def public_result(payload: dict) -> dict:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"result_token", "map_url"}
    }


def capture(base_url: str, output: Path, cases: list[str]) -> dict:
    manifest = verify_installed(ROOT)
    descriptor = json.loads((ROOT / "local-release.json").read_text())
    assert manifest["planner_sha256"] == descriptor["planner_sha256"]
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True
    )
    report = {
        "schema": 1,
        "captured_utc": datetime.now(UTC).isoformat(),
        "transport": "HTTP to Waitress on loopback",
        "release": manifest["version"],
        "planner_sha256": planner_hash(ROOT),
        "asset_sha256": descriptor["sha256"],
        "candidate_sha256": manifest["dataset"]["candidate_sha256"],
        "road": descriptor["road"],
        "base_commit": revision.stdout.strip() if revision.returncode == 0 else None,
        "cache_description": (
            "First and repeated requests in one fresh server process; first requests "
            "can share data loaded by readiness and earlier cases."
        ),
        "runs": [],
    }
    private_maps = {}
    passed = True
    with httpx.Client(base_url=base_url, timeout=25, trust_env=False) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 200, "Server readiness failed"
        report["readiness_http_status"] = ready.status_code
        for name in cases:
            for state in ("first-request", "repeated-request"):
                began = time.perf_counter()
                response = client.post("/api/v1/trips", json=CASES[name])
                elapsed = time.perf_counter() - began
                payload = response.json()
                run = {
                    "case": name,
                    "state": state,
                    "request": CASES[name],
                    "http_status": response.status_code,
                    "elapsed_seconds": round(elapsed, 4),
                    "response": public_result(payload),
                }
                valid = response.status_code == 200
                if valid:
                    saved = client.get(
                        f"/api/v1/trips/{payload['trip_id']}",
                        headers={"Authorization": "Bearer " + payload["result_token"]},
                    )
                    map_path = payload["map_url"].split("#", 1)[0]
                    map_response = client.get(map_path)
                    count = len(payload["fuel_stops"])
                    expected_stops = (
                        count == 0
                        if name in {"short-trip", "saved-map"}
                        else count >= 1
                        if name == "single-stop"
                        else count >= 2
                    )
                    run.update(
                        {
                            "saved_result_http_status": saved.status_code,
                            "saved_result_matches": saved.json()
                            == public_result(payload),
                            "map_http_status": map_response.status_code,
                            "map_path": map_path,
                            "stop_count": count,
                            "station_ids": [
                                stop["station_id"] for stop in payload["fuel_stops"]
                            ],
                            "fuel_purchases_cost": payload["costs"][
                                "fuel_purchases_cost"
                            ],
                            "within_15_seconds": elapsed <= 15,
                            "within_driving_allowance": Decimal(
                                payload["route"]["extra_duration_seconds"]
                            )
                            <= Decimal(
                                CASES[name].get("max_extra_duration_minutes", "30")
                            )
                            * 60,
                            "expected_stop_count": expected_stops,
                        }
                    )
                    valid = all(
                        (
                            saved.status_code == 200,
                            run["saved_result_matches"],
                            map_response.status_code == 200,
                            run["within_15_seconds"],
                            run["within_driving_allowance"],
                            expected_stops,
                        )
                    )
                    if state == "first-request":
                        private_maps[name] = base_url.rstrip("/") + payload["map_url"]
                run["status"] = "passed" if valid else "failed"
                report["runs"].append(run)
                passed &= valid
                print(
                    json.dumps(
                        {
                            key: run[key]
                            for key in (
                                "case",
                                "state",
                                "http_status",
                                "elapsed_seconds",
                                "status",
                            )
                        }
                    )
                )
        report["map_script_http_status"] = client.get(
            "/static/app/trip_map.js"
        ).status_code
        passed &= report["map_script_http_status"] == 200
    report["status"] = "passed" if passed else "failed"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    private_path = ROOT / "var/verification/live-map-links.json"
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_text(json.dumps(private_maps, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "var/private-development/records/docs/evidence/live-delivery.json",
    )
    parser.add_argument("--case", action="append", choices=CASES)
    args = parser.parse_args()
    report = capture(
        args.base_url,
        args.output,
        args.case or ["short-trip", "single-stop", "multiple-stops"],
    )
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
