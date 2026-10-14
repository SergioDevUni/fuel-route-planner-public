"""Check prepared identity, actual zero-trip API and repeat-setup preservation."""

import argparse
import json
import os
import subprocess
import sys

from scripts.local_common import ROOT, load_local_env, sha256, verify_installed


def verify(*, expect_clean: bool = False) -> dict:
    load_local_env()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("ORS_API_KEY", "non-live-readiness-sentinel")
    manifest = verify_installed()
    import django

    django.setup()
    from django.conf import settings
    from django.db import connections
    from django.test import Client, override_settings

    from app.api.models import TripResult
    from app.planning.candidates import CandidateIndex
    from app.stations.models import DatasetVersion

    active = DatasetVersion.objects.get(status="active")
    index = CandidateIndex(ROOT / "var/station_candidates", active.source_sha256)
    assert len(index.stations) == manifest["dataset"]["us_offers"]
    before = TripResult.objects.count()
    if expect_clean and before:
        raise ValueError("Packaged seed contains saved trips")
    with override_settings(ALLOWED_HOSTS=[*settings.ALLOWED_HOSTS, "testserver"]):
        client = Client()
        assert client.get("/health/ready").status_code == 200
        point = {"type": "coordinates", "latitude": 41, "longitude": -87}
        created = client.post(
            "/api/v1/trips",
            json.dumps({"start": point, "finish": point}),
            content_type="application/json",
        )
        assert created.status_code == 200, "Actual zero-trip API failed"
        payload = created.json()
        assert payload["status"] == "zero_distance"
        fetched = client.get(
            "/api/v1/trips/" + payload["trip_id"],
            HTTP_AUTHORIZATION="Bearer " + payload["result_token"],
        )
        assert fetched.status_code == 200
        assert fetched.json()["route"] == payload["route"]
        assert client.get(payload["map_url"].split("#", 1)[0]).status_code == 200
    environment_hash = sha256(ROOT / ".env")
    saved_id = payload["trip_id"]
    connections.close_all()
    repeated = subprocess.run(
        [sys.executable, "-m", "scripts.setup_local"],
        cwd=ROOT,
        check=False,
    )
    assert repeated.returncode == 0, "Repeat setup failed"
    assert sha256(ROOT / ".env") == environment_hash
    assert TripResult.objects.filter(public_id=saved_id).exists()
    report = {
        "status": "passed",
        "version": manifest["version"],
        "us_offers": len(index.stations),
        "road_points": manifest["road"]["points"],
        "completed_road_sources": manifest["road"]["completed_sources"],
        "zero_trip_creation": True,
        "saved_retrieval": True,
        "map_shell": True,
        "repeat_setup_preserves_results_and_env": True,
        "hosted_calls": 0,
    }
    output = ROOT / "var/verification/local-install.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-clean", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(expect_clean=args.expect_clean), indent=2))


if __name__ == "__main__":
    main()
