"""Presentation delivery keeps trip details and local records separate."""

import json
from pathlib import Path
from zipfile import ZipFile

from app.api.serialization import public_trip_payload
from scripts import package_release
from scripts.local_common import sha256


def test_public_trip_preserves_plan_and_does_not_mutate_cached_diagnostics():
    payload = {
        "status": "verified",
        "fuel_stops": [{"station_id": "csv-1", "purchase_gallons": "10"}],
        "costs": {"fuel_purchases_cost": "30.00"},
        "assumptions": {
            "road_legs_verified": True,
            "station_selection": "local_road_recovery",
            "optimization_evidence": {"searches": [{"stopped_by_time": True}]},
            "optimization_interrupted": True,
        },
    }
    public = public_trip_payload(payload)
    assert public["fuel_stops"] == payload["fuel_stops"]
    assert public["costs"] == payload["costs"]
    assert public["assumptions"] == {
        "road_legs_verified": True,
        "station_selection": "fuel_cost",
    }
    assert payload["assumptions"]["optimization_interrupted"] is True
    assert payload["assumptions"]["station_selection"] == "local_road_recovery"


def test_package_keeps_only_showcase_assets_and_excludes_private_records(
    tmp_path, monkeypatch
):
    root = tmp_path / "source"
    root.mkdir()
    for name in package_release.SOURCE_FILES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("example\n")
    for name in (
        *(f"docs/{name}" for name in package_release.PRESENTATION_DOCS),
        "docs/NATIONWIDE_VALIDATION.md",
        "docs/presentation/SCRIPT.md",
        "docs/presentation/PROMPTS.md",
        "docs/presentation/images/01-problem.png",
        "docs/LOCAL_SETUP.md",
        "docs/images/map.png",
        "docs/evidence/acceptance.json",
        "docs/REMEDIATION_LOG.md",
        "var/private-development/records/observations.json",
    ):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("example\n")
    asset = root / "assets.zip"
    with ZipFile(asset, "w") as archive:
        archive.writestr("data/raw/offers.csv", "station,price\n")
    descriptor = {
        "version": "presentation",
        "asset": "assets.zip",
        "bytes": asset.stat().st_size,
        "sha256": sha256(asset),
        "planner_sha256": "planner",
        "road": {},
    }
    (root / "local-release.json").write_text(json.dumps(descriptor))
    monkeypatch.setattr(package_release, "planner_hash", lambda _: "planner")
    result = package_release.package(root, tmp_path / "delivery", bundle=asset)
    assert result["version"] == "presentation"
    with ZipFile(
        Path(result["release_directory"]) / "fuel-route-planner-presentation.zip"
    ) as archive:
        names = archive.namelist()
        prefix = "fuel-route-planner-presentation/"
        packaged_docs = {
            name.removeprefix(prefix + "docs/")
            for name in names
            if name.startswith(prefix + "docs/")
        }
        assert packaged_docs == package_release.PRESENTATION_DOCS
        assert {name for name in names if name.endswith(".md")} == {
            prefix + "README.md"
        }
        assert not any("docs/evidence/" in name for name in names)
        assert not any("private-development" in name for name in names)
        assert not any("REMEDIATION_LOG" in name for name in names)
