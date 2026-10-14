"""Shared epa branch fallback sites rules used by review and preparation tools."""

import json
import re
from collections import defaultdict
from pathlib import Path

from app.stations.catalogs.epa_ust_site_catalog import usable_coordinate
from app.stations.catalogs.exit_coordinate_catalog import distance_miles
from app.stations.catalogs.overture_identity import road_numbers
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.files import sha256 as sha256

CSV_COORDINATES = "var/station_locations/csv_coordinate_catalog.json"

PREVIOUS = (
    "var/station_locations/exit_coordinate_catalog.json",
    "var/station_locations/exit_fuel_sites.json",
    "var/station_locations/reviewed_city_fuel_sites.json",
    "var/station_locations/reviewed_branch_sites.json",
)

OUTPUT = "var/station_locations/epa_branch_fallback_sites.json"

MAX_CITY_DISTANCE_MILES = 10.0

GENERIC = {
    "and",
    "center",
    "convenience",
    "dba",
    "express",
    "food",
    "fuel",
    "gas",
    "inc",
    "llc",
    "market",
    "mart",
    "oil",
    "service",
    "station",
    "stop",
    "store",
    "the",
    "travel",
    "truck",
}


def branch(name: str) -> int | None:
    matches = re.findall(r"#\s*(\d{2,7})\b", name)
    return int(matches[0]) if len(matches) == 1 else None


def numbers(name: str) -> set[int]:
    return {int(value) for value in re.findall(r"(?<!\d)\d{2,7}(?!\d)", name)}


def identity_words(name: str) -> set[str]:
    words = re.findall(r"[a-z]+", name.lower().replace("'", ""))
    return {
        word.removesuffix("s")
        for word in words
        if len(word) >= 3 and word not in GENERIC
    }


def credible_pair(station: dict, facility: dict, branch_number: int) -> bool:
    csv_road = road_numbers(station["address"])
    facility_road = road_numbers(facility["Address"] or "")
    same_road = bool(csv_road & facility_road)
    same_brand = bool(
        identity_words(station["name"]) & identity_words(facility["Name"])
    )
    if branch_number < 100:
        return same_road and same_brand
    return branch_number >= 1000 or same_road or same_brand


def build_catalog(root: Path = ROOT) -> dict:
    csv_path = root / CSV_COORDINATES
    fallback = json.loads(csv_path.read_text())
    previous_paths = [root / name for name in PREVIOUS]
    excluded = {
        item["opis_id"]
        for path in previous_paths
        for item in json.loads(path.read_text())["records"]
    }
    by_state: dict[str, list[tuple[dict, int]]] = defaultdict(list)
    for station in fallback["records"]:
        if station["country"] != "US" or station["opis_id"] in excluded:
            continue
        number = branch(station["name"])
        if number is not None:
            by_state[station["state"]].append((station, number))
    records = []
    source_hashes = {}
    for state, stations in sorted(by_state.items()):
        source_name = f"var/epa_ust/{state.lower()}_facilities.ndjson"
        path = root / source_name
        if not path.exists():
            continue
        source_hashes[source_name] = sha256(path)
        index: dict[int, list[dict]] = defaultdict(list)
        for line in path.open(encoding="utf-8"):
            facility = json.loads(line)
            if facility["Facility_Status"] != "Open UST(s)" or not usable_coordinate(
                facility
            ):
                continue
            for number in numbers(facility["Name"] or ""):
                index[number].append(facility)
        for station, number in stations:
            candidates = [
                facility
                for facility in index.get(number, [])
                if distance_miles(
                    station["latitude"],
                    station["longitude"],
                    facility["Latitude"],
                    facility["Longitude"],
                )
                <= MAX_CITY_DISTANCE_MILES
            ]
            if len(candidates) != 1:
                continue
            facility = candidates[0]
            if not credible_pair(station, facility, number):
                continue
            records.append(
                {
                    "opis_id": station["opis_id"],
                    "country": "US",
                    "site_id": f"epa-ust:{facility['Facility_ID']}",
                    "facility_name": facility["Name"],
                    "facility_address": facility["Address"],
                    "facility_city": facility["City"],
                    "latitude": facility["Latitude"],
                    "longitude": facility["Longitude"],
                    "location_precision": "site_candidate",
                }
            )
    return {
        "schema_version": 1,
        "source_sha256": fallback["source_sha256"],
        "csv_coordinate_catalog_sha256": sha256(csv_path),
        "previous_hashes": {name: sha256(root / name) for name in PREVIOUS},
        "source_hashes": source_hashes,
        "max_city_distance_miles": MAX_CITY_DISTANCE_MILES,
        "records": sorted(records, key=lambda item: int(item["opis_id"])),
    }


def validate_catalog(root: Path, source_hash: str) -> dict:
    catalog = json.loads((root / OUTPUT).read_text())
    if catalog["source_sha256"] != source_hash or catalog != build_catalog(root):
        raise ValueError("EPA branch fallback differs from pinned facilities")
    return catalog
