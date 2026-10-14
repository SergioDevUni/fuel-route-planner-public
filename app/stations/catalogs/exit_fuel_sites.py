"""Shared exit fuel sites rules used by review and preparation tools."""

import csv
import io
import json
import re
import tarfile
from collections import defaultdict
from pathlib import Path

from app.domain.routing import Coordinates
from app.routing.geometry import geodesic_meters
from app.stations.catalogs.exit_coordinate_catalog import OUTPUT as EXIT_CATALOG
from app.stations.catalogs.exit_coordinate_catalog import RELEASE, sha256
from app.stations.catalogs.paths import ROOT

OUTPUT = "var/station_locations/exit_fuel_sites.json"

REVIEWED_NAMES = {
    "557": "Sapp Bros Travel Center",
    "65989": "Dancing Eagle Travel Center",
    "70740": "Jack Flash",
    "71255": "Fuel n Treat",
}

REVIEWED_SOURCE_SITES = {
    "46": {
        "path": "var/overture_states/al_places.ndjson",
        "record_id": "cf2f30c1-8fb0-4425-88f4-d9e2f54b322a",
        "name": "Petro 2 Travel Center",
        "address": "428 Main St",
        "evidence_url": "https://www.ta-petro.com/location/al/petro-shorter/",
    },
    "56": {
        "path": "var/epa_ust/nd_facilities.ndjson",
        "record_id": "ND403",
        "name": "Cenex Tops Car and Truck Stop",
        "address": "31346 NE 27th Ave",
        "evidence_url": "https://www.iexitapp.com/North%20Dakota/I-94/Exit%20182/29803?order=rank",
    },
    "69895": {
        "path": "var/overture_states/il_places.ndjson",
        "record_id": "efe096ed-1406-4b10-8cc6-3cbe4c666ada",
        "name": "Citgo - Minooka",
        "address": "17845 N Ridge Rd",
        "csv_name": "Shell",
        "state": "IL",
        "category": "gas_station",
        "evidence_url": "https://find.shell.com/us/fuel/12839384-17845-ridge-rd/en_PH",
    },
    "71906": {
        "path": "var/overture_states/in_places.ndjson",
        "record_id": "fe976128-1abc-49ba-be4a-5684fe922150",
        "name": "Compass Travel Center",
        "address": "11715 IN-10",
        "csv_name": "COMPASS TRAVEL CENTER",
        "state": "IN",
        "category": "travel_service",
        "evidence_url": "https://compasstravelcenter.com/",
    },
    "52430": {
        "path": "var/overture_states/ga_places.ndjson",
        "record_id": "2befd483-ce06-455e-9ab8-0f3836eb8f99",
        "name": "Chevron",
        "address": "6860 Battlefield Parkway",
        "csv_name": "CHEVRON FOOD MART",
        "state": "GA",
        "category": "gas_station",
        "evidence_url": "https://www.iexitapp.com/business/Chevron/234727?esmid=45701&order=rank&origin=by+state",
    },
    "71798": {
        "path": "var/overture_states/wa_places.ndjson",
        "record_id": "337e0589-673f-45b8-9a61-f66e95e10e2d",
        "name": "Space Age Fuel Travel Center A&W Burgers Castle Rock Wa Exit 52",
        "address": "9515 Old Pacific Hwy N",
        "csv_name": "Space Age #202",
        "state": "WA",
        "category": "travel_service",
        "evidence_url": "https://www.swcleanair.gov/docs/permits/Final/24-3639TSD.pdf",
    },
}


def _rows(archive: tarfile.TarFile, suffix: str) -> list[dict]:
    member = next(item for item in archive.getmembers() if item.name.endswith(suffix))
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"OpenInterstate {suffix} missing")
    return list(csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8")))


def build_catalog(root: Path = ROOT) -> dict:
    exit_path = root / EXIT_CATALOG
    catalog = json.loads(exit_path.read_text())
    by_id = {item["opis_id"]: item for item in catalog["records"]}
    if (set(REVIEWED_NAMES) | set(REVIEWED_SOURCE_SITES)) - set(by_id):
        raise ValueError("reviewed CSV exit missing")
    wanted_exits = {by_id[opis_id]["exit_id"] for opis_id in REVIEWED_NAMES}
    with tarfile.open(root / RELEASE, "r:gz") as archive:
        links = defaultdict(list)
        for item in _rows(archive, "/csv/exit_place_links.csv"):
            if item["exit_id"] in wanted_exits and item["category"] == "gas":
                links[item["exit_id"]].append(item)
        wanted_places = {item["place_id"] for group in links.values() for item in group}
        places = {
            item["place_id"]: item
            for item in _rows(archive, "/csv/places.csv")
            if item["place_id"] in wanted_places
        }
    records = []
    for opis_id, expected_name in REVIEWED_NAMES.items():
        exit_record = by_id[opis_id]
        matches = [
            (item, places[item["place_id"]])
            for item in links[exit_record["exit_id"]]
            if item["place_id"] in places
            and places[item["place_id"]]["name"].casefold() == expected_name.casefold()
            and float(item["distance_m"]) <= 1000
        ]
        if len(matches) != 1:
            raise ValueError(f"distinctive fuel place not unique: {opis_id}")
        link, place = matches[0]
        point = json.loads(place["geometry_geojson"])
        if point["type"] != "Point" or len(point["coordinates"]) != 2:
            raise ValueError(f"fuel place geometry differs: {opis_id}")
        longitude, latitude = point["coordinates"]
        records.append(
            {
                "opis_id": opis_id,
                "country": "US",
                "site_id": f"openstreetmap:{place['place_id']}",
                "place_name": place["name"],
                "exit_id": exit_record["exit_id"],
                "exit_distance_meters": float(link["distance_m"]),
                "latitude": latitude,
                "longitude": longitude,
                "location_precision": "site_candidate",
            }
        )
    source_hashes = {}
    for opis_id, review in REVIEWED_SOURCE_SITES.items():
        path = root / review["path"]
        source_hashes[review["path"]] = sha256(path)
        is_overture = review["path"].endswith("_places.ndjson")
        field = "id" if is_overture else "Facility_ID"
        source = next(
            (
                item
                for line in path.open(encoding="utf-8")
                if (item := json.loads(line))[field] == review["record_id"]
            ),
            None,
        )
        if source is None:
            raise ValueError(f"reviewed fuel point missing: {opis_id}")
        if is_overture:
            address = source["addresses"][0]["freeform"]
            name = source["name"]
            match = re.fullmatch(
                r"POINT \((-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)\)",
                source["point_wkt"],
            )
            if match is None or source["operating_status"] != "open":
                raise ValueError("reviewed mapped point differs")
            longitude, latitude = float(match.group(1)), float(match.group(2))
            if "state" in review and (
                source["addresses"][0]["region"] != review["state"]
                or source["basic_category"] != review["category"]
                or by_id[opis_id]["name"] != review["csv_name"]
                or by_id[opis_id]["state"] != review["state"]
            ):
                raise ValueError(f"reviewed fuel source differs: {opis_id}")
        else:
            address = source["Address"]
            name = source["Name"]
            latitude, longitude = source["Latitude"], source["Longitude"]
            if source["Facility_Status"] != "Open UST(s)":
                raise ValueError("Tops facility point differs")
        if address != review["address"] or name != review["name"]:
            raise ValueError(f"reviewed fuel identity differs: {opis_id}")
        exit_record = by_id[opis_id]
        exit_distance = geodesic_meters(
            Coordinates(exit_record["latitude"], exit_record["longitude"]),
            Coordinates(latitude, longitude),
        )
        if exit_distance > 1000:
            raise ValueError(f"reviewed fuel point is too far from exit: {opis_id}")
        records.append(
            {
                "opis_id": opis_id,
                "country": "US",
                "site_id": (
                    f"overture:{review['record_id']}"
                    if is_overture
                    else f"epa-ust:{review['record_id']}"
                ),
                "place_name": name,
                "exit_id": exit_record["exit_id"],
                "exit_distance_meters": round(float(exit_distance), 1),
                "latitude": latitude,
                "longitude": longitude,
                "location_precision": "site_candidate",
                "evidence_url": review["evidence_url"],
            }
        )
    return {
        "schema_version": 1,
        "source_sha256": catalog["source_sha256"],
        "exit_catalog_sha256": sha256(exit_path),
        "openinterstate_release_sha256": sha256(root / RELEASE),
        "reviewed_source_hashes": source_hashes,
        "records": sorted(records, key=lambda item: int(item["opis_id"])),
    }


def validate_catalog(root: Path, source_hash: str) -> dict:
    catalog = json.loads((root / OUTPUT).read_text())
    if catalog["source_sha256"] != source_hash or catalog != build_catalog(root):
        raise ValueError("exit fuel sites differ from reviewed source")
    return catalog
