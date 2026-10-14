import json

import pytest

from scripts.build_exit_fuel_sites import OUTPUT, ROOT, validate_catalog


@pytest.mark.integration
@pytest.mark.skipif(not (ROOT / OUTPUT).exists(), reason="local exit sites missing")
def test_reviewed_exit_fuel_sites_replay_from_pinned_release() -> None:
    catalog = json.loads((ROOT / OUTPUT).read_text())
    records = validate_catalog(ROOT, catalog["source_sha256"])["records"]
    assert {item["opis_id"] for item in records} == {
        "46",
        "56",
        "52430",
        "557",
        "65989",
        "70740",
        "71255",
        "71798",
        "69895",
        "71906",
    }
    assert all(item["location_precision"] == "site_candidate" for item in records)
    assert all(item.get("exit_distance_meters", 0) < 1000 for item in records)
