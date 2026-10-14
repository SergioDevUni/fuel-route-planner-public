import json

import pytest

from scripts.verify_loves_operator import verify_record, visible_opis_codes


def _page(opis_code: str) -> str:
    store = {
        "name": "Love's Travel Stop in Atkinson, IL. Store #766",
        "address": {
            "streetAddress": "5 South State Street",
            "addressLocality": "Atkinson",
            "addressRegion": "IL",
            "addressCountry": "US",
        },
        "geo": {"latitude": 41.410645, "longitude": -90.016349},
    }
    return (
        '<script type="application/ld+json">'
        + json.dumps(store)
        + "</script><li>OPIS<!-- -->: <!-- -->"
        + opis_code
        + "</li>"
    )


def test_operator_code_branch_and_coordinate_all_agree() -> None:
    record = {
        "opis_id": "135",
        "store_number": "766",
        "city": "Atkinson",
        "state": "IL",
        "evidence_url": "https://www.loves.com/locations/example",
        "expected_address": "5 South State Street",
        "latitude": 41.410645,
        "longitude": -90.016349,
    }
    rows = [
        {
            "Truckstop Name": "LOVES TRAVEL STOP #766",
            "City": "Atkinson",
            "State": "IL",
        }
    ]
    result = verify_record(record, rows, _page("00135"))
    assert result["site_status"] == "verified"
    assert result["routing_status"] == "pending_check"
    assert visible_opis_codes(_page("00135")) == {135}
    with pytest.raises(ValueError, match="operator OPIS codes differ"):
        verify_record(record, rows, _page("00136"))


def test_source_branch_mismatch_cannot_be_saved_by_correct_opis() -> None:
    record = {
        "opis_id": "135",
        "store_number": "766",
        "city": "Atkinson",
        "state": "IL",
    }
    rows = [
        {
            "Truckstop Name": "LOVES TRAVEL STOP #767",
            "City": "Atkinson",
            "State": "IL",
        }
    ]
    with pytest.raises(ValueError, match="source branch"):
        verify_record(record, rows, _page("00135"))


def test_conflicting_alias_rows_block_identity_verification() -> None:
    record = {
        "opis_id": "135",
        "store_number": "766",
        "city": "Atkinson",
        "state": "IL",
    }
    rows = [
        {"Truckstop Name": "LOVES #766", "City": "Atkinson", "State": "IL"},
        {"Truckstop Name": "LOVES #767", "City": "Atkinson", "State": "IL"},
    ]
    with pytest.raises(ValueError, match="source branch"):
        verify_record(record, rows, _page("00135"))
