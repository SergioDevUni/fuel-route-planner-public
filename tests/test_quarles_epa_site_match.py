"""Quarles public facility joins require both branch and guide address."""

from scripts.build_quarles_epa_site_catalog import (
    branch_suffix_matches,
    matching_facilities,
)


def facility(name: str, address: str = "4545 Progress Rd") -> dict:
    return {
        "Name": name,
        "Address": address,
        "Zip_Code": "23502",
        "Latitude": 36.86455,
        "Longitude": -76.23356,
        "Coordinate_Source": "Geocode",
        "Address_Match_Type": "PointAddress",
    }


def test_branch_suffix_accepts_q_card_alias_but_not_unrelated_number() -> None:
    assert branch_suffix_matches("4907", "Q-Card #907")
    assert branch_suffix_matches("4403", "QFN #403")
    assert not branch_suffix_matches("4907", "Q-Card #908")
    assert not branch_suffix_matches("4907", "Other Store #907")


def test_facility_requires_public_guide_street_and_zip() -> None:
    guide = {"address": "4545 Progress Road 23502"}
    good = facility("Q-Card #907")
    assert matching_facilities("4907", guide, [good]) == [good]
    assert (
        matching_facilities("4907", guide, [facility("Q-Card #907", "4545 Other Rd")])
        == []
    )
    assert matching_facilities("4907", guide, [dict(good, Zip_Code="23503")]) == []
