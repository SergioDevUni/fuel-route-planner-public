import csv
import math
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

from app.stations.catalogs.overture_identity import road_numbers
from app.stations.catalogs.paths import ROOT, SOURCE

ROWS = ROOT / "var/operator_review/comdata_rows.json"
PDF = ROOT / "var/operator_review/comdata_gallonup_2024.pdf"
OUTPUT = ROOT / "var/operator_review/comdata_site_catalog.json"
OVERTURE = ROOT / "var/overture_states"
FUEL_CATEGORIES = {"gas_station", "convenience_store", "truck_stop", "fueling_station"}
REPLACEMENTS = {
    "hwy": "highway",
    "hiway": "highway",
    "rd": "road",
    "st": "street",
    "ave": "avenue",
    "blvd": "boulevard",
    "rte": "route",
    "rt": "route",
    "co": "county",
    "ct": "court",
    "dr": "drive",
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
}


def plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(re.findall(r"[a-z0-9]", normalized))


def identity_name(value: str) -> str:
    """Ignore only trailing legal-entity suffixes when linking directory names."""
    words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", value.casefold()))
    if words and words[-1] in {"llc", "inc", "ltd"}:
        words.pop()
    return "".join(words)


def place_identity_name(value: str) -> str:
    """A directory branch number may be absent from the mapped site label."""
    return identity_name(re.sub(r"\s*#\s*\d{2,6}\s*$", "", value))


def address_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return {
        REPLACEMENTS.get(token, token) for token in re.findall(r"[a-z0-9]+", normalized)
    }


def house_number(value: str) -> str | None:
    match = re.match(r"^\s*(\d{2,6})\b", value)
    return match.group(1) if match else None


def point_coordinates(wkt: str) -> tuple[float, float] | None:
    match = re.fullmatch(r"POINT \((-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)\)", wkt)
    return (float(match.group(2)), float(match.group(1))) if match else None


def distance_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat_delta = math.radians(b[0] - a[0])
    lon_delta = math.radians(b[1] - a[1])
    x = (
        math.sin(lat_delta / 2) ** 2
        + math.cos(math.radians(a[0]))
        * math.cos(math.radians(b[0]))
        * math.sin(lon_delta / 2) ** 2
    )
    return 3958.7613 * 2 * math.asin(math.sqrt(x))


def unique_public_row(station: dict, by_locality: dict) -> dict | None:
    matches = {
        row["location_code"]: row
        for state in station["states"]
        for city in station["cities"]
        for row in by_locality.get((state, plain(city)), ())
        if identity_name(row["name"])
        in {identity_name(name) for name in station["names"]}
    }
    return next(iter(matches.values())) if len(matches) == 1 else None


def unique_public_state_row(station: dict, by_state_name: dict) -> dict | None:
    matches = {
        (row["page"], row["line"], row["location_code"]): row
        for state in station["states"]
        for name in station["names"]
        for row in by_state_name.get((state, identity_name(name)), ())
    }
    return next(iter(matches.values())) if len(matches) == 1 else None


def csv_state_name_ids() -> dict[tuple[str, str], set[str]]:
    ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            ids[
                (row["State"].strip().upper(), identity_name(row["Truckstop Name"]))
            ].add(row["OPIS Truckstop ID"].strip())
    return ids


def choose_place(
    row: dict, places: list[dict], *, locality_alias: bool = False
) -> tuple[dict, list[str]] | None:
    house = house_number(row["address"])
    if house is None:
        return None
    source_tokens = address_tokens(row["address"])
    matches = []
    for place in places:
        fuel_category = place["basic_category"] in FUEL_CATEGORIES
        exact_site_name = place_identity_name(
            place["name"] or ""
        ) == place_identity_name(row["name"])
        if (
            not (fuel_category or exact_site_name)
            or (place["confidence"] or 0) < 0.75
            or place["operating_status"] not in {None, "open"}
        ):
            continue
        coordinates = point_coordinates(place["point_wkt"])
        if coordinates is None:
            continue
        for address in place["addresses"] or []:
            if (
                address.get("region") != row["state"]
                or address.get("country") != "US"
                or house_number(address.get("freeform") or "") != house
            ):
                continue
            same_locality = plain(address.get("locality") or "") == plain(row["city"])
            if same_locality == locality_alias:
                continue
            if locality_alias and not exact_site_name:
                continue
            address_value = address.get("freeform") or ""
            overlap = len(source_tokens & address_tokens(address_value)) / max(
                1, len(source_tokens | address_tokens(address_value))
            )
            postcode = re.match(r"\d{5}", address.get("postcode") or "")
            if locality_alias and (
                postcode is None or postcode.group(0) != row["postcode"]
            ):
                continue
            source_routes = road_numbers(row["address"])
            mapped_routes = road_numbers(address_value) | set(
                re.findall(
                    rf"\b{re.escape(row['state'])}-(\d{{1,3}})\b", address_value.upper()
                )
            )
            route_alias = exact_site_name and bool(source_routes & mapped_routes)
            compact_address = plain(row["address"]) == plain(address_value)
            if (
                (overlap >= 0.75 or compact_address)
                if locality_alias
                else (overlap >= 0.6 or route_alias or compact_address)
            ) and (
                not re.fullmatch(r"\d{5}", row["postcode"])
                or postcode is None
                or postcode.group(0) == row["postcode"]
            ):
                matches.append((place, address, coordinates, overlap))
                break
    if not matches:
        return None
    fuel_matches = [
        item for item in matches if item[0]["basic_category"] in FUEL_CATEGORIES
    ]
    if fuel_matches:
        matches = fuel_matches
    first = matches[0][2]
    if any(distance_miles(first, item[2]) > 0.12 for item in matches):
        return None
    best = max(
        matches,
        key=lambda item: (
            SequenceMatcher(
                None, plain(row["name"]), plain(item[0]["name"] or "")
            ).ratio(),
            item[3],
            item[0]["confidence"],
            item[0]["id"],
        ),
    )
    return best[0], sorted({item[0]["id"] for item in matches})
