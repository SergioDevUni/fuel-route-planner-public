"""Guard facility matching against coincidental branch numbers and city aliases."""

from collections import Counter

from scripts.build_epa_ust_site_catalog import (
    choose_fuzzy_chain_city,
    select,
    state_index,
)


def facility(
    name: str, city: str, object_id: int, latitude: float, longitude: float
) -> dict:
    return {
        "OBJECTID": object_id,
        "Name": name,
        "Address": "123 Main St",
        "City": city,
        "Latitude": latitude,
        "Longitude": longitude,
        "Coordinate_Source": "State",
        "Address_Match_Type": None,
        "Facility_Status": "Open UST(s)",
    }


def test_matching_branch_number_in_unrelated_business_is_rejected() -> None:
    station = {
        "names": ["QUARLES #4911"],
        "cities": ["Virginia Beach"],
        "addresses": ["US-13"],
    }
    indices = state_index(
        [facility("Auto Zone Store #4911", "Virginia Beach", 1, 36.85, -76.1)]
    )
    centers = {
        ("VA", "virginia beach"): [
            {"id": "virginia-beach", "latitude": 36.85, "longitude": -76.1}
        ]
    }
    assert select(station, "VA", indices, centers, Counter()) is None


def test_exact_branch_in_adjacent_municipality_retains_csv_city() -> None:
    station = {
        "names": ["CIRCLE K #2723748"],
        "cities": ["Pearl"],
        "addresses": ["US-80"],
    }
    indices = state_index([facility("Circle K #2723748", "Jackson", 1, 32.273, -90.13)])
    centers = {
        ("MS", "pearl"): [{"id": "pearl", "latitude": 32.273, "longitude": -90.13}]
    }
    selected = select(station, "MS", indices, centers, Counter())
    assert selected is not None
    assert selected[2:4] == ("Pearl", "branch_nearby_city")


def test_same_brand_prefix_with_exact_branch_and_city() -> None:
    station = {
        "names": ["CASEYS #3434"],
        "cities": ["Denver"],
        "addresses": ["US-63"],
    }
    indices = state_index(
        [facility("CASEYS GENERAL STORE 3434", "Denver", 2, 42.67, -92.34)]
    )
    selected = select(station, "IA", indices, {}, Counter())
    assert selected is not None
    assert selected[2:4] == ("Denver", "branch_city")


def test_generic_first_word_cannot_substitute_for_brand() -> None:
    station = {"names": ["FUEL STORE #4911"], "cities": ["Denver"], "addresses": []}
    indices = state_index([facility("FUEL DEPOT #4911", "Denver", 3, 42.67, -92.34)])
    assert select(station, "IA", indices, {}, Counter()) is None


def test_fuzzy_facility_does_not_swap_numbered_branches_or_directions() -> None:
    station = {
        "names": ["TA KNOXVILLE EAST TRAVEL CENTER #170"],
        "cities": ["Knoxville"],
        "addresses": [],
    }
    csv_counts = Counter({("TN", "knoxville", "ta knoxville east 170"): 1})
    west = facility("Knoxville West Travel Center #170", "Knoxville", 1, 36, -84)
    west["Facility_Status"] = "Open UST(s)"
    city_facilities = state_index([west])[-1]
    assert choose_fuzzy_chain_city(station, "TN", city_facilities, csv_counts) is None
