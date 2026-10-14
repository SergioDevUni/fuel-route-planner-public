import json

import pytest

from scripts.build_reviewed_city_fuel_sites import OUTPUT, ROOT, validate_catalog


@pytest.mark.integration
@pytest.mark.skipif(not (ROOT / OUTPUT).exists(), reason="local mapped places missing")
def test_reviewed_city_fuel_sites_replay_from_pinned_map_records() -> None:
    catalog = json.loads((ROOT / OUTPUT).read_text())
    records = validate_catalog(ROOT, catalog["source_sha256"])["records"]
    assert {record["opis_id"] for record in records} == {
        "243",
        "2331",
        "68043",
        "70154",
        "71957",
        "71966",
        "72082",
        "72855",
    }
    assert {record["location_precision"] for record in records} == {"site_candidate"}
    assert all(record["site_id"].startswith("overture:") for record in records)
