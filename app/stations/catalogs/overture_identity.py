"""Accept uniquely corroborated CSV identities from pinned place extracts."""

import csv
import json
import math
import re
from collections import defaultdict
from difflib import SequenceMatcher

from app.stations.catalogs.names import name_similarity, tokens
from app.stations.catalogs.paths import OVERTURE_ROOT as OUTPUT_ROOT
from app.stations.catalogs.paths import ROOT, SOURCE

OUTPUT = OUTPUT_ROOT / "exact_sites.json"
CITY_PLACES = ROOT / "data/prepared/locations/places.ndjson"
NEARBY_CITY_MILES = 10.0
ALLOWED_CATEGORIES = {
    "gas_station",
    "convenience_store",
    "truck_stop",
    "fueling_station",
}
GENERIC_SITE_WORDS = {
    "fuel",
    "gas",
    "station",
    "truck",
    "stop",
    "store",
    "travel",
    "center",
    "market",
    "mart",
    "food",
    "convenience",
    "the",
    "and",
    "of",
    "inc",
    "llc",
    "plus",
    "service",
    "express",
    "plaza",
    "site",
    "oil",
    "energy",
    "hwy",
    "highway",
}


def normalized(value: str) -> str:
    return " ".join(sorted(tokens(value)))


def chain_name(value: str) -> str:
    """Canonical chain label, excluding a CSV-only branch and generic store suffixes."""
    value = re.sub(r"#\s*\d{1,7}\b", " ", value.casefold())
    value = re.sub(r"\b([a-z]+)'s\b", r"\1s", value)
    words = re.findall(r"[a-z0-9]+", value)
    generic = {
        "convenience",
        "country",
        "food",
        "store",
        "travel",
        "center",
        "truck",
        "stop",
        "fuel",
        "gas",
        "station",
        "inc",
        "llc",
        "corp",
    }
    return " ".join(word for word in words if word not in generic)


def unique_chain_city_matches(station: dict, state: str, places: list[dict]) -> dict:
    """Match a chain's single CSV site to its single addressed city fuel point."""
    names = {chain_name(name) for name in station["names"]}
    names.discard("")
    cities = {city.casefold().strip() for city in station["cities"]}
    branches = {
        int(number)
        for name in station["names"]
        for number in re.findall(r"#\s*(\d{1,7})\b", name)
    }
    matches = {}
    for place in places:
        name = place["name"] or ""
        mapped_branch = {int(n) for n in re.findall(r"#\s*(\d{1,7})\b", name)}
        if (
            chain_name(name) not in names
            or place["basic_category"] not in ALLOWED_CATEGORIES
            or (place["confidence"] or 0) < 0.85
            or place["operating_status"] not in {None, "open"}
            or (mapped_branch and mapped_branch != branches)
        ):
            continue
        for address in place["addresses"] or []:
            if (
                address.get("region") == state
                and address.get("country") == "US"
                and (address.get("locality") or "").casefold().strip() in cities
                and address.get("freeform")
            ):
                matches[place["id"]] = (place, address)
                break
    return matches


def fuzzy_chain_city_matches(station: dict, state: str, places: list[dict]) -> dict:
    """Find one local fuel site with a distinctive shared name token."""
    cities = {city.casefold().strip() for city in station["cities"]}
    branches = {
        int(number)
        for name in station["names"]
        for number in re.findall(r"#\s*(\d{1,7})\b", name)
    }
    matches = {}
    for place in places:
        name = place["name"] or ""
        mapped_branch = {int(n) for n in re.findall(r"#\s*(\d{1,7})\b", name)}
        mapped_base = chain_name(name)
        source_name, similarity = max(
            (
                (
                    source_name,
                    SequenceMatcher(None, chain_name(source_name), mapped_base).ratio(),
                )
                for source_name in station["names"]
            ),
            key=lambda item: item[1],
        )
        distinctive = {
            word for word in chain_name(source_name).split() if len(word) >= 4
        } & {word for word in mapped_base.split() if len(word) >= 4}
        if (
            similarity < 0.75
            or not distinctive
            or place["basic_category"] not in ALLOWED_CATEGORIES
            or (place["confidence"] or 0) < 0.85
            or place["operating_status"] not in {None, "open"}
            or (mapped_branch and mapped_branch != branches)
        ):
            continue
        for address in place["addresses"] or []:
            if (
                address.get("region") == state
                and address.get("country") == "US"
                and (address.get("locality") or "").casefold().strip() in cities
                and address.get("freeform")
            ):
                matches[place["id"]] = (
                    place,
                    address,
                    source_name,
                    round(similarity, 4),
                )
                break
    return matches


def road_numbers(value: str) -> set[str]:
    return set(
        re.findall(
            r"\b(?:US|SR|ST|RT|RTE|ROUTE|HWY|HIGHWAY|I|INTERSTATE)[-\s]*(\d{1,3})\b",
            value.upper(),
        )
    )


def branch_number(value: str) -> str | None:
    matches = re.findall(r"#\s*(\d{2,7})\b", value)
    return matches[0] if len(matches) == 1 else None


def branch_name_similarity(source_name: str, place_name: str) -> float:
    return SequenceMatcher(
        None, normalized(source_name), normalized(place_name)
    ).ratio()


def csv_road_site_matches(station: dict, state: str, places: list[dict]) -> dict:
    """Find city/road/name site evidence without requiring identical names."""
    csv_roads = set().union(*(road_numbers(value) for value in station["addresses"]))
    if not csv_roads:
        return {}
    csv_cities = {city.casefold().strip() for city in station["cities"]}
    city_words = set().union(*(tokens(city) for city in station["cities"]))
    csv_words = set().union(*(tokens(name) for name in station["names"]))
    branches = {
        match
        for name in station["names"]
        for match in re.findall(r"#\s*(\d{1,7})\b", name)
    }
    matches = {}
    for place in places:
        place_name = place["name"] or ""
        similarity = name_similarity(station["names"], place_name)
        if (
            place["basic_category"] not in ALLOWED_CATEGORIES
            or (place["confidence"] or 0) < 0.8
            or place["operating_status"] not in {None, "open"}
            or similarity < 0.65
        ):
            continue
        place_numbers = set(re.findall(r"(?<!\d)\d{2,7}(?!\d)", place_name))
        if branches and place_numbers and not branches & place_numbers:
            continue
        identifying = (
            (csv_words & tokens(place_name))
            - GENERIC_SITE_WORDS
            - city_words
            - csv_roads
        )
        if not identifying:
            continue
        for address in place["addresses"] or []:
            if (
                address.get("region") == state
                and address.get("country") == "US"
                and (address.get("locality") or "").casefold().strip() in csv_cities
                and csv_roads & road_numbers(address.get("freeform") or "")
            ):
                matches[place["id"]] = (place, address, round(similarity, 4))
                break
    return matches


def csv_distinctive_city_matches(station: dict, state: str, places: list[dict]) -> dict:
    """Find uniquely named local sites when a CSV exit has no site street."""
    csv_cities = {city.casefold().strip() for city in station["cities"]}
    city_words = set().union(*(tokens(city) for city in station["cities"]))
    csv_words = set().union(*(tokens(name) for name in station["names"]))
    branches = {
        match
        for name in station["names"]
        for match in re.findall(r"#\s*(\d{1,7})\b", name)
    }
    matches = {}
    for place in places:
        name = place["name"] or ""
        similarity = name_similarity(station["names"], name)
        if (
            place["basic_category"] not in ALLOWED_CATEGORIES
            or (place["confidence"] or 0) < 0.85
            or place["operating_status"] not in {None, "open"}
            or name.casefold().startswith("atm")
            or similarity < 0.85
        ):
            continue
        place_words = tokens(name)
        place_numbers = set(re.findall(r"(?<!\d)\d{1,7}(?!\d)", name))
        if branches and place_numbers and not branches & place_numbers:
            continue
        distinctive = {
            word
            for word in ((csv_words & place_words) - GENERIC_SITE_WORDS - city_words)
            if word.isalpha() and len(word) >= 3
        }
        if len(distinctive) < 2:
            continue
        directions = {"north", "south", "east", "west"}
        csv_directions = (csv_words - city_words) & directions
        place_directions = (place_words - city_words) & directions
        if csv_directions and csv_directions != place_directions:
            continue
        for address in place["addresses"] or []:
            if (
                address.get("region") == state
                and address.get("country") == "US"
                and (address.get("locality") or "").casefold().strip() in csv_cities
                and address.get("freeform")
            ):
                matches[place["id"]] = (place, address, round(similarity, 4))
                break
    return matches


def distance_miles(first: tuple[float, float], second: tuple[float, float]) -> float:
    latitude = math.radians(second[0] - first[0])
    longitude = math.radians(second[1] - first[1])
    x = (
        math.sin(latitude / 2) ** 2
        + math.cos(math.radians(first[0]))
        * math.cos(math.radians(second[0]))
        * math.sin(longitude / 2) ** 2
    )
    return 3958.7613 * 2 * math.asin(math.sqrt(x))


def city_centers() -> dict[tuple[str, str], list[dict]]:
    index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    with CITY_PLACES.open(encoding="utf-8") as stream:
        for line in stream:
            place = json.loads(line)
            for alias in {name.casefold() for name in place["aliases"]}:
                index[(place["state"], alias)].append(place)
    return index


def nearby_city(
    state: str,
    cities: list[str],
    point: tuple[float, float],
    centers: dict[tuple[str, str], list[dict]],
    max_miles: float = NEARBY_CITY_MILES,
) -> tuple[str, str, float] | None:
    options = [
        (
            city,
            center["id"],
            distance_miles(point, (center["latitude"], center["longitude"])),
        )
        for city in cities
        for center in centers.get((state, city.casefold()), [])
    ]
    if not options:
        return None
    result = min(options, key=lambda item: (item[2], item[1]))
    return result if result[2] <= max_miles else None


def nearby_branch_candidates(
    places: list[dict], state: str, station: dict, centers: dict
) -> list[tuple[dict, str, tuple[str, str, float], float]]:
    matches = []
    for place in places:
        if (
            place["basic_category"] not in ALLOWED_CATEGORIES
            or (place["confidence"] or 0) < 0.8
            or place["operating_status"] not in {None, "open"}
            or not any(
                address.get("region") == state and address.get("country") == "US"
                for address in place["addresses"] or []
            )
        ):
            continue
        place_numbers = set(re.findall(r"(?<!\d)\d{2,6}(?!\d)", place["name"] or ""))
        source_names = [
            name
            for name in station["names"]
            if branch_number(name) is not None
            and place_numbers == {branch_number(name)}
            and branch_name_similarity(name, place["name"] or "") >= 0.9
        ]
        if not source_names:
            continue
        point = re.fullmatch(
            r"POINT \((-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)\)",
            place["point_wkt"],
        )
        if point is None:
            continue
        city_match = nearby_city(
            state,
            station["cities"],
            (float(point.group(2)), float(point.group(1))),
            centers,
            max_miles=8.0,
        )
        if city_match is None:
            continue
        best_name = max(
            source_names,
            key=lambda name: branch_name_similarity(name, place["name"] or ""),
        )
        matches.append(
            (
                place,
                best_name,
                city_match,
                branch_name_similarity(best_name, place["name"] or ""),
            )
        )
    return matches


def source_stations() -> dict[str, dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            grouped[row["OPIS Truckstop ID"].strip()].append(row)
    return {
        opis_id: {
            "names": sorted({row["Truckstop Name"].strip() for row in rows}),
            "cities": sorted({row["City"].strip() for row in rows}),
            "states": sorted({row["State"].strip().upper() for row in rows}),
            "addresses": sorted({row["Address"].strip() for row in rows}),
        }
        for opis_id, rows in grouped.items()
    }
