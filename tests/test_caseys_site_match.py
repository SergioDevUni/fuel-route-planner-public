"""Guard exact numbered-store address matching against false site links."""

import pytest

from scripts.build_caseys_site_catalog import choose_site, parsed_url, road_match


def test_official_url_exposes_state_street_and_branch() -> None:
    assert parsed_url(
        "https://www.caseys.com/general-store/ia-toledo/113-hwy-30-w/2653"
    ) == ("IA", "113 hwy 30 w", "2653")


def test_non_official_url_is_rejected() -> None:
    with pytest.raises(ValueError):
        parsed_url("https://example.com/general-store/ia-toledo/113-hwy-30-w/2653")


@pytest.mark.parametrize(
    ("official", "mapped", "expected"),
    [
        ("113 hwy 30 w", "113 Highway 30 W", True),
        ("1101 jefferson ave", "1101 Jefferson Ave", True),
        ("113 hwy 30 w", "114 Highway 30 W", False),
        ("113 hwy 30 w", "113 Main St", False),
        ("113 hwy 30 w", "113 30th Street", False),
        ("21w415 north ave", "21W415 North Avenue", True),
    ],
)
def test_road_requires_house_and_road_evidence(
    official: str, mapped: str, expected: bool
) -> None:
    assert road_match(official, mapped) is expected


def test_competing_points_at_one_official_address_remain_unresolved() -> None:
    def place(place_id: str, latitude: float) -> dict:
        return {
            "id": place_id,
            "name": "Casey's",
            "confidence": 0.95,
            "operating_status": "open",
            "point_wkt": f"POINT (-92.0 {latitude})",
            "addresses": [
                {
                    "region": "IA",
                    "country": "US",
                    "freeform": "1101 Jefferson Ave",
                }
            ],
        }

    centers = {("IA", "denver"): [{"id": "city", "latitude": 42.0, "longitude": -92.0}]}
    assert (
        choose_site(
            [place("one", 42.0), place("two", 42.01)],
            "IA",
            "1101 jefferson ave",
            ["Denver"],
            centers,
        )
        is None
    )
