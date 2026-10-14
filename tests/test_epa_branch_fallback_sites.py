import json

import pytest

from scripts.build_epa_branch_fallback_sites import OUTPUT, ROOT, validate_catalog


@pytest.mark.integration
@pytest.mark.skipif(not (ROOT / OUTPUT).exists(), reason="local EPA facilities missing")
def test_epa_branch_fallback_replays_distinct_store_points() -> None:
    catalog = json.loads((ROOT / OUTPUT).read_text())
    records = validate_catalog(ROOT, catalog["source_sha256"])["records"]
    by_id = {record["opis_id"]: record for record in records}
    assert len(records) == len(by_id) == 43
    assert not {"63959", "66461", "67750", "68029", "72350"} & set(by_id)
    assert by_id["6442"]["facility_address"] == "2831 Highway 84 West"
    assert by_id["1344"]["facility_address"] == "193 STATE HWY 17 N"
    assert by_id["69698"]["site_id"] != by_id["70188"]["site_id"]
    assert by_id["69698"]["facility_name"].endswith("(East)")
    assert by_id["70188"]["facility_name"].endswith("(West)")
