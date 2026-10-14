"""Guard exact branch evidence against number and brand confusion."""

from app.stations.catalogs.overture_identity import (
    branch_name_similarity,
    branch_number,
    csv_distinctive_city_matches,
    csv_road_site_matches,
    fuzzy_chain_city_matches,
    nearby_branch_candidates,
    nearby_city,
    unique_chain_city_matches,
)


def test_branch_number_requires_one_explicit_store_identifier() -> None:
    assert branch_number("ASAP GENERAL STORE #330") == "330"
    assert branch_number("CIRCLE K #2741499") == "2741499"
    assert branch_number("I-40 EXIT 330") is None
    assert branch_number("CIRCLE K #708 / #709") is None


def test_csv_chain_city_can_fill_missing_map_branch_but_not_conflicting_branch() -> (
    None
):
    station = {"names": ["ALLSUPS CONVENIENCE STORE #2414"], "cities": ["Lubbock"]}

    def place(site_id: str, name: str) -> dict:
        return {
            "id": site_id,
            "name": name,
            "basic_category": "gas_station",
            "confidence": 0.93,
            "operating_status": "open",
            "addresses": [
                {
                    "region": "TX",
                    "country": "US",
                    "locality": "Lubbock",
                    "freeform": "100 Main Street",
                }
            ],
        }

    assert set(
        unique_chain_city_matches(station, "TX", [place("site", "Allsup's")])
    ) == {"site"}
    assert (
        unique_chain_city_matches(station, "TX", [place("wrong", "Allsup's #2415")])
        == {}
    )
    assert (
        len(
            unique_chain_city_matches(
                station, "TX", [place("one", "Allsup's"), place("two", "Allsup's")]
            )
        )
        == 2
    )
    fuzzy = place("fuzzy", "Jordans Kwik Marts")
    fuzzy["addresses"][0]["locality"] = "Hahira"
    assert set(
        fuzzy_chain_city_matches(
            {"names": ["JORDANS KWIK MART"], "cities": ["Hahira"]},
            "TX",
            [fuzzy],
        )
    ) == {"fuzzy"}


def test_similar_brand_spelling_can_match_without_accepting_other_brand() -> None:
    assert branch_name_similarity("FAS MART #5134", "FASMART 5134") >= 0.9
    assert branch_name_similarity("CIRCLE K #708", "OTHER MART #708") < 0.9


def test_nearby_city_accepts_township_label_but_rejects_distant_same_road() -> None:
    centers = {
        ("NJ", "columbia"): [
            {"id": "columbia", "latitude": 40.92621, "longitude": -75.09268}
        ],
        ("IN", "michigan city"): [
            {"id": "michigan-city", "latitude": 41.70754, "longitude": -86.89503}
        ],
    }
    near = nearby_city("NJ", ["Columbia"], (40.89753, -75.070323), centers)
    assert near is not None and near[0:2] == ("Columbia", "columbia")
    assert nearby_city("NJ", ["Columbia"], (41.02621, -75.09268), centers)
    assert nearby_city("NJ", ["Columbia"], (41.02621, -75.09268), centers, 5.0) is None
    assert nearby_city("IN", ["Michigan City"], (41.6, -85.9), centers) is None


def test_nearby_branch_requires_number_brand_and_city_proximity() -> None:
    centers = {
        ("MO", "platte city"): [
            {"id": "platte", "latitude": 39.37, "longitude": -94.78}
        ]
    }
    station = {"names": ["TREX MART #17"], "cities": ["Platte City"]}

    def place(name: str, longitude: float) -> dict:
        return {
            "name": name,
            "basic_category": "gas_station",
            "confidence": 0.9,
            "operating_status": "open",
            "addresses": [{"region": "MO", "country": "US"}],
            "point_wkt": f"POINT ({longitude} 39.37)",
        }

    matches = nearby_branch_candidates(
        [
            place("Trex Mart #17", -94.78),
            place("Trex Mart #18", -94.78),
            place("Other Mart #17", -94.78),
            place("Trex Mart #17", -95.0),
        ],
        "MO",
        station,
        centers,
    )
    assert len(matches) == 1
    assert matches[0][0]["name"] == "Trex Mart #17"


def test_csv_road_site_uses_location_clues_without_exact_name() -> None:
    station = {
        "names": ["CIRCLE K #2706632"],
        "cities": ["Selma"],
        "addresses": ["US-80/SR-8 & CR-480"],
    }

    def place(place_id: str, name: str, road: str) -> dict:
        return {
            "id": place_id,
            "name": name,
            "basic_category": "gas_station",
            "confidence": 0.9,
            "operating_status": "open",
            "addresses": [
                {"region": "AL", "country": "US", "locality": "Selma", "freeform": road}
            ],
        }

    matches = csv_road_site_matches(
        station,
        "AL",
        [
            place("site", "Circle K", "4865 US Hwy 80 E"),
            place("other-road", "Circle K", "120 Main St"),
            place("other-branch", "Circle K #2709999", "150 US Hwy 80 E"),
        ],
    )
    assert set(matches) == {"site"}
    assert (
        len(
            csv_road_site_matches(
                station,
                "AL",
                [
                    place("site", "Circle K", "4865 US Hwy 80 E"),
                    place("second", "Circle K", "200 US Hwy 80 E"),
                ],
            )
        )
        == 2
    )


def test_distinctive_city_site_rejects_conflicting_branches_and_competitors() -> None:
    station = {
        "names": ["JORDANS KWIK STOP #5"],
        "cities": ["Portia"],
        "addresses": ["I-40 EXIT 216"],
    }

    def place(place_id: str, name: str) -> dict:
        return {
            "id": place_id,
            "name": name,
            "basic_category": "gas_station",
            "confidence": 0.9,
            "operating_status": "open",
            "addresses": [
                {
                    "region": "AR",
                    "country": "US",
                    "locality": "Portia",
                    "freeform": "2917 US-63",
                }
            ],
        }

    assert set(
        csv_distinctive_city_matches(
            station,
            "AR",
            [
                place("site", "Jordans Kwik Stop"),
                place("other-branch", "Jordans Kwik Stop #52"),
            ],
        )
    ) == {"site"}
    assert (
        len(
            csv_distinctive_city_matches(
                station,
                "AR",
                [
                    place("site", "Jordans Kwik Stop"),
                    place("competitor", "Jordans Kwik Stop"),
                ],
            )
        )
        == 2
    )
