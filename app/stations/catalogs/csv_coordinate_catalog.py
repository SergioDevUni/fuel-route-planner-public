"""Shared csv coordinate catalog rules used by review and preparation tools."""

import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path

from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.files import sha256 as sha256

FALLBACK_IDS = "data/review/csv_coordinate_fallback_ids.json"

CSV_LOCATIONS = "var/station_locations/csv_location_catalog.json"

US_PLACES = "data/prepared/locations/places.ndjson"

CA_ARCHIVE = "var/geonames/CA.zip"

OUTPUT = "var/station_locations/csv_coordinate_catalog.json"

CANADIAN_PROVINCES = {
    "AB": "01",
    "BC": "02",
    "MB": "03",
    "NB": "04",
    "NL": "05",
    "NS": "07",
    "ON": "08",
    "PE": "09",
    "QC": "10",
    "SK": "11",
    "YT": "12",
    "NT": "13",
    "NU": "14",
}

ROAD_RESOLVED_PLACES = {
    "2192": "geonames:4815651",  # Mount Nebo, WV, US-19/SR-129
    "64406": "geonames:4720455",  # Prairie Hill, TX, US-84/FM-73
    "64825": "geonames:4195894",  # Fortson, GA, I-185 exit 19
    "67763": "geonames:4437357",  # Mount Pleasant, MS, US-72
    "68311": "geonames:4465585",  # Evergreen, NC, SR-242
    "70966": "geonames:4752250",  # Chesterfield, VA, SR-10/SR-145
    "70970": "geonames:4780220",  # Powhatan, VA, US-60/SR-603
    "67570": "geonames:5976991",  # Hornby, ON; two nearby records
}

OTHER_LOCALITIES = {
    "66598": "geonames:4551673",  # S Coffeyville -> South Coffeyville, OK
    "70284": "geonames:4961498",  # Town of Corinth, ME (admin point)
    "71242": "geonames:5321154",  # Willow Beach, AZ (named area)
}


def compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _places(root: Path, wanted: set[tuple[str, str]]) -> dict:
    by_name: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    by_id: dict[str, dict] = {}
    with (root / US_PLACES).open(encoding="utf-8") as stream:
        for line in stream:
            place = json.loads(line)
            names = {compact(name) for name in place["aliases"]}
            keys = {(place["state"], name) for name in names} & wanted
            for key in keys:
                by_name[key][place["id"]] = place
            if place["id"] in ROAD_RESOLVED_PLACES.values():
                by_id[place["id"]] = place
    with zipfile.ZipFile(root / CA_ARCHIVE) as archive:
        for line in archive.read("CA.txt").decode("utf-8").splitlines():
            fields = line.split("\t")
            if fields[6] != "P":
                continue
            place = {
                "id": f"geonames:{fields[0]}",
                "name": fields[1],
                "aliases": [fields[1], fields[2], *fields[3].split(",")],
                "state": fields[10],
                "latitude": float(fields[4]),
                "longitude": float(fields[5]),
                "population": int(fields[14] or 0),
            }
            keys = {
                (place["state"], compact(name)) for name in place["aliases"]
            } & wanted
            for key in keys:
                by_name[key][place["id"]] = place
            if place["id"] in ROAD_RESOLVED_PLACES.values():
                by_id[place["id"]] = place
    # The US endpoint gazetteer intentionally contains populated places only.
    # Two CSV locality labels need a named administrative/area feature instead.
    with zipfile.ZipFile(root / "var/downloads/US.zip") as archive:
        wanted_ids = set(OTHER_LOCALITIES.values()) - set(by_id)
        for line in archive.open("US.txt"):
            fields = line.decode("utf-8").rstrip("\n").split("\t")
            place_id = f"geonames:{fields[0]}"
            if place_id not in wanted_ids:
                continue
            by_id[place_id] = {
                "id": place_id,
                "name": fields[1],
                "state": fields[10],
                "latitude": float(fields[4]),
                "longitude": float(fields[5]),
                "population": int(fields[14] or 0),
            }
    return by_name, by_id


def build_catalog(root: Path = ROOT, ids: set[str] | None = None) -> dict:
    fallback_ids = json.loads((root / FALLBACK_IDS).read_text())
    csv_locations = json.loads((root / CSV_LOCATIONS).read_text())
    locations = {item["opis_id"]: item for item in csv_locations["records"]}
    if ids is None:
        ids = set(fallback_ids["station_ids"])
    if ids - set(locations):
        raise ValueError("fallback IDs absent from CSV location catalog")
    wanted = {
        (
            locations[opis_id]["state"]
            if locations[opis_id]["country"] == "US"
            else CANADIAN_PROVINCES[locations[opis_id]["state"]],
            compact(locations[opis_id]["city"]),
        )
        for opis_id in ids
    }
    by_name, by_id = _places(root, wanted)
    records = []
    for opis_id in sorted(ids, key=int):
        csv = locations[opis_id]
        region = (
            csv["state"] if csv["country"] == "US" else CANADIAN_PROVINCES[csv["state"]]
        )
        candidates = list(by_name.get((region, compact(csv["city"])), {}).values())
        if opis_id in OTHER_LOCALITIES:
            place = by_id[OTHER_LOCALITIES[opis_id]]
        elif opis_id in ROAD_RESOLVED_PLACES:
            place_id = ROAD_RESOLVED_PLACES[opis_id]
            place = next((p for p in candidates if p["id"] == place_id), None)
            if place is None:
                raise ValueError(f"road-resolved locality missing: {opis_id}")
        elif candidates:
            place = max(
                candidates,
                key=lambda p: (
                    compact(p["name"]) == compact(csv["city"]),
                    p["population"],
                    -int(p["id"].split(":")[1]),
                ),
            )
        else:
            raise ValueError(f"CSV locality not found in GeoNames: {opis_id}")
        if place["state"] != region:
            raise ValueError(f"locality province/state differs: {opis_id}")
        records.append(
            {
                "opis_id": opis_id,
                "country": csv["country"],
                "name": csv["names"][0],
                "address": csv["address"],
                "city": csv["city"],
                "state": csv["state"],
                "site_id": place["id"],
                "place_name": place["name"],
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "location_precision": "city_representative",
                "location_source": "geonames",
            }
        )
    if len({item["opis_id"] for item in records}) != len(records):
        raise ValueError("fallback catalog repeats a CSV ID")
    return {
        "schema_version": 1,
        "source_sha256": fallback_ids["source_sha256"],
        "fallback_ids_sha256": sha256(root / FALLBACK_IDS),
        "csv_location_catalog_sha256": sha256(root / CSV_LOCATIONS),
        "us_places_sha256": sha256(root / US_PLACES),
        "ca_archive_sha256": sha256(root / CA_ARCHIVE),
        "us_archive_sha256": sha256(root / "var/downloads/US.zip"),
        "records": sorted(records, key=lambda item: int(item["opis_id"])),
    }


def validate_catalog(root: Path, source_hash: str) -> dict:
    catalog = json.loads((root / OUTPUT).read_text())
    if (
        catalog["source_sha256"] != source_hash
        or catalog["csv_location_catalog_sha256"] != sha256(root / CSV_LOCATIONS)
        or catalog["us_places_sha256"] != sha256(root / US_PLACES)
        or catalog["ca_archive_sha256"] != sha256(root / CA_ARCHIVE)
        or catalog["us_archive_sha256"] != sha256(root / "var/downloads/US.zip")
        or catalog["fallback_ids_sha256"] != sha256(root / FALLBACK_IDS)
    ):
        raise ValueError("CSV location-point sources changed")
    locations = {
        item["opis_id"]: item
        for item in json.loads((root / CSV_LOCATIONS).read_text())["records"]
    }
    seen = set()
    for item in catalog["records"]:
        opis_id = item["opis_id"]
        if opis_id in seen or opis_id not in locations:
            raise ValueError("duplicate or unknown CSV location-point ID")
        seen.add(opis_id)
        csv = locations[opis_id]
        if (
            item["address"] != csv["address"]
            or item["city"] != csv["city"]
            or item["state"] != csv["state"]
            or item["country"] != csv["country"]
            or item["location_precision"] != "city_representative"
            or item["location_source"] != "geonames"
            or not -90 <= item["latitude"] <= 90
            or not -180 <= item["longitude"] <= 180
        ):
            raise ValueError(f"CSV location-point record differs: {opis_id}")
    frozen_ids = set(json.loads((root / FALLBACK_IDS).read_text())["station_ids"])
    if seen != frozen_ids:
        raise ValueError("CSV location-point IDs differ from frozen fallback set")
    if catalog != build_catalog(root, seen):
        raise ValueError("CSV location-point selection differs from GeoNames")
    return catalog
