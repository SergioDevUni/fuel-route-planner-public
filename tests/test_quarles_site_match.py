"""Quarles site links require a unique fuel point at the public address."""

from scripts.build_quarles_site_catalog import choose_place, guide_address


def place(address: str, postcode: str = "20601") -> dict:
    return {
        "id": "point-1",
        "name": "Quarles Fleet Fueling",
        "basic_category": "gas_station",
        "confidence": 0.9,
        "operating_status": "open",
        "point_wkt": "POINT (-76.88 38.62)",
        "addresses": [
            {
                "freeform": address,
                "postcode": postcode,
                "region": "MD",
                "country": "US",
            }
        ],
    }


def test_guide_address_accepts_single_digit_and_letter_suffix() -> None:
    assert guide_address("2 Southlawn Court 20850") == (
        "2 Southlawn Court",
        "20850",
    )
    assert guide_address("1393A Hwy 80 East 79604") == (
        "1393A Hwy 80 East",
        "79604",
    )


def test_choose_place_requires_unique_matching_fuel_point() -> None:
    row = {"address": "12090 Acton Lane 20601"}
    right = place("12090 Acton Ln")
    assert choose_place(row, "MD", [right]) == right
    assert choose_place(row, "MD", [right, dict(right, id="point-2")]) is None
    assert choose_place(row, "MD", [place("12090 Acton Ln", "20602")]) is None
    assert choose_place(row, "MD", [dict(right, name="Other Fuel")]) == dict(
        right, name="Other Fuel"
    )


def test_choose_place_rejects_separate_sites_at_one_public_address() -> None:
    row = {"address": "12090 Acton Lane 20601"}
    first = dict(place("12090 Acton Ln"), name="Shell", confidence=0.95)
    second = dict(
        place("12090 Acton Ln"),
        id="point-2",
        name="Exxon",
        confidence=0.85,
        point_wkt="POINT (-76.89 38.62)",
    )
    assert choose_place(row, "MD", [first, second]) is None


def test_choose_place_prefers_one_co_located_high_confidence_alias() -> None:
    row = {"address": "12090 Acton Lane 20601"}
    first = dict(place("12090 Acton Ln"), name="Shell", confidence=0.95)
    second = dict(
        place("12090 Acton Ln"),
        id="point-2",
        name="Fuel Stop",
        confidence=0.85,
        point_wkt="POINT (-76.88005 38.62002)",
    )
    assert choose_place(row, "MD", [first, second]) == first
