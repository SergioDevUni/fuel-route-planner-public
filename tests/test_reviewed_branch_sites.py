import json

import pytest

from scripts.build_reviewed_branch_sites import OUTPUT, ROOT, validate_catalog


@pytest.mark.integration
@pytest.mark.skipif(not (ROOT / OUTPUT).exists(), reason="local fuel sources missing")
def test_csv_branches_replay_to_separate_mapped_sites() -> None:
    catalog = json.loads((ROOT / OUTPUT).read_text())
    records = validate_catalog(ROOT, catalog["source_sha256"])["records"]
    by_id = {record["opis_id"]: record for record in records}
    assert set(by_id) == {
        "3230",
        "63959",
        "66186",
        "66440",
        "66461",
        "67750",
        "68029",
        "6954",
        "70255",
        "71227",
        "71769",
        "72350",
    }
    assert by_id["3230"]["site_id"] != by_id["66186"]["site_id"]
    assert by_id["3230"]["mapped_address"] == "112 N US HIGHWAY 281"
    assert by_id["66186"]["mapped_address"] == "500 E RICE ST"
    assert by_id["6954"]["mapped_address"] == "198 RT 17 S"
    assert by_id["71227"]["mapped_address"] == "19280 TX-16"
    assert by_id["67750"]["mapped_address"] == "2305 Fm 511"
    assert by_id["72350"]["mapped_address"] == "504 N Crego Rd"
    assert by_id["63959"]["mapped_address"] == "3S405 State Route 59"
    assert by_id["66440"]["mapped_address"] == "4701 S Central Ave"
    assert by_id["66461"]["mapped_address"] == "110 Arnett St"
    assert by_id["68029"]["mapped_address"] == "6672 Alabama Hwy"
    assert by_id["70255"]["mapped_address"] == "2560 E M-55"
    assert by_id["71769"]["mapped_address"] == "1100 S. NC 581"
