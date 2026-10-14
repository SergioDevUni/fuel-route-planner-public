"""Street corroboration must reject a wrong road or a split physical site."""

from app.stations.catalogs.comdata_matching import (
    choose_place,
    unique_public_row,
    unique_public_state_row,
)

ROW = {
    "state": "MI",
    "city": "Bridgman",
    "name": "CASEYS #3956",
    "address": "10299 RED ARROW HIGHWAY",
    "postcode": "49106",
}


def place(place_id: str, address: str, longitude: float) -> dict:
    return {
        "id": place_id,
        "name": "Casey's",
        "basic_category": "gas_station",
        "confidence": 0.95,
        "operating_status": "open",
        "point_wkt": f"POINT ({longitude} 41.94)",
        "addresses": [
            {
                "region": "MI",
                "country": "US",
                "locality": "Bridgman",
                "postcode": "49106",
                "freeform": address,
            }
        ],
    }


def test_matching_house_number_on_wrong_road_is_not_accepted() -> None:
    wrong = place("wrong", "10299 Lake Street", -86.56)
    assert choose_place(ROW, [wrong]) is None


def test_matching_address_with_far_apart_points_is_not_accepted() -> None:
    first = place("first", "10299 Red Arrow Hwy", -86.56)
    second = place("second", "10299 Red Arrow Hwy", -86.53)
    assert choose_place(ROW, [first, second]) is None


def test_colocated_duplicates_keep_one_site_and_all_evidence_ids() -> None:
    first = place("first", "10299 Red Arrow Hwy", -86.56)
    second = place("second", "10299 Red Arrow Highway", -86.5601)
    chosen = choose_place(ROW, [first, second])
    assert chosen is not None
    assert chosen[1] == ["first", "second"]


def test_legal_suffix_links_unique_directory_identity() -> None:
    station = {
        "states": ["OK"],
        "cities": ["Big Cabin"],
        "names": ["WOODSHED OF BIG CABIN"],
    }
    row = {"location_code": "OK131", "name": "WOODSHED OF BIG CABIN LLC"}
    assert unique_public_row(station, {("OK", "bigcabin"): [row]}) == row


def test_legal_suffix_does_not_resolve_duplicate_directory_sites() -> None:
    station = {
        "states": ["OK"],
        "cities": ["Big Cabin"],
        "names": ["WOODSHED OF BIG CABIN"],
    }
    rows = [
        {"location_code": "OK131", "name": "WOODSHED OF BIG CABIN LLC"},
        {"location_code": "OK132", "name": "WOODSHED OF BIG CABIN INC"},
    ]
    assert unique_public_row(station, {("OK", "bigcabin"): rows}) is None


def test_unique_state_name_can_find_adjacent_city_directory_row() -> None:
    station = {
        "states": ["VA"],
        "cities": ["Mount Jackson"],
        "names": ["SHEETZ #701"],
    }
    row = {
        "page": 1,
        "line": 2,
        "location_code": "VA701",
        "name": "SHEETZ #701",
        "city": "MT. JACKSON",
    }
    assert unique_public_state_row(station, {("VA", "sheetz701"): [row]}) == row
    other = {**row, "line": 3, "location_code": "VA702"}
    assert unique_public_state_row(station, {("VA", "sheetz701"): [row, other]}) is None


def test_exact_name_can_corroborate_site_when_place_category_is_restaurant() -> None:
    restaurant = place("caseys", "10299 Red Arrow Highway", -86.56)
    restaurant["basic_category"] = "restaurant"
    assert choose_place(ROW, [restaurant]) == (restaurant, ["caseys"])


def test_unrelated_name_with_nonfuel_category_is_rejected() -> None:
    restaurant = place("other", "10299 Red Arrow Highway", -86.56)
    restaurant["name"] = "Unrelated Restaurant"
    restaurant["basic_category"] = "restaurant"
    assert choose_place(ROW, [restaurant]) is None


def test_matching_nonfuel_place_does_not_displace_fuel_evidence() -> None:
    fuel = place("fuel", "10299 Red Arrow Highway", -86.56)
    restaurant = place("restaurant", "10299 Red Arrow Highway", -86.53)
    restaurant["basic_category"] = "restaurant"
    assert choose_place(ROW, [restaurant, fuel]) == (fuel, ["fuel"])


def test_exact_site_and_route_number_accept_street_route_alias() -> None:
    row = {
        "state": "CO",
        "city": "Alamosa",
        "name": "LOAF N JUG #750010",
        "address": "102 HIGHWAY 160 EAST",
        "postcode": "81101",
    }
    candidate = place("alias", "102 US-160", -105.87)
    candidate["name"] = "Loaf 'N Jug"
    candidate["addresses"][0].update(
        {"region": "CO", "locality": "Alamosa", "postcode": "81101"}
    )
    assert choose_place(row, [candidate]) == (candidate, ["alias"])
    candidate["addresses"][0]["freeform"] = "102 US-285"
    assert choose_place(row, [candidate]) is None


def test_compact_street_spelling_accepts_same_address() -> None:
    row = {
        "state": "OH",
        "city": "Quaker City",
        "name": "FUEL MART #727",
        "address": "21927 BRIDGE WATER RD",
        "postcode": "43773",
    }
    candidate = place("fuel", "21927 Bridgewater Rd", -81.3275)
    candidate["name"] = "Fuel Mart"
    candidate["addresses"][0].update(
        {"region": "OH", "locality": "Quaker City", "postcode": "43773-9788"}
    )
    assert choose_place(row, [candidate]) == (candidate, ["fuel"])


def test_exact_site_accepts_state_highway_alias_but_not_other_route() -> None:
    row = {
        "state": "MO",
        "city": "Bowling Green",
        "name": "AYERCO #35",
        "address": "18048 HWY 161",
        "postcode": "63334",
    }
    candidate = place("fuel", "18048 MO-161", -91.1785)
    candidate["name"] = "Ayerco"
    candidate["addresses"][0].update(
        {"region": "MO", "locality": "Bowling Green", "postcode": "63334"}
    )
    assert choose_place(row, [candidate]) == (candidate, ["fuel"])
    candidate["addresses"][0]["freeform"] = "18048 MO-162"
    assert choose_place(row, [candidate]) is None


def test_locality_alias_requires_same_brand_street_and_zip() -> None:
    row = {
        "state": "PA",
        "city": "Blairsville",
        "name": "SHEETZ #498",
        "address": "610 ROUTE 22 HWY W",
        "postcode": "15717",
    }
    candidate = place("sheetz", "610 Route 22 Hwy W", -79.23)
    candidate["name"] = "Sheetz"
    candidate["addresses"][0].update(
        {"region": "PA", "locality": "Burrell Twp", "postcode": "15717"}
    )
    assert choose_place(row, [candidate]) is None
    assert choose_place(row, [candidate], locality_alias=True) == (
        candidate,
        ["sheetz"],
    )
    candidate["addresses"][0]["postcode"] = "15718"
    assert choose_place(row, [candidate], locality_alias=True) is None
    candidate["addresses"][0]["postcode"] = "15717"
    candidate["name"] = "Unrelated Fuel"
    assert choose_place(row, [candidate], locality_alias=True) is None
