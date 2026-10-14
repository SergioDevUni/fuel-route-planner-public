"""Shared reviewed city fuel sites rules used by review and preparation tools."""

import json
import re
from pathlib import Path

from app.stations.catalogs.exit_coordinate_catalog import distance_miles
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.files import sha256 as sha256

CSV_COORDINATES = "var/station_locations/csv_coordinate_catalog.json"

EXIT_CATALOG = "var/station_locations/exit_coordinate_catalog.json"

OUTPUT = "var/station_locations/reviewed_city_fuel_sites.json"

REVIEWS = {
    "243": {
        "state": "IN",
        "record_id": "e99680c8-6a20-4651-a046-e25cd8181b6a",
        "address": "3347 W State Road 10",
        "map_name": "Phil's Truck Stop",
        "evidence_url": "https://www.in.gov/fssa/dmha/files/inspection-results-Jan2020.pdf",
        "corroborating_url": "https://www.iexitapp.com/business/Save/633952",
    },
    "2331": {
        "state": "VA",
        "record_id": "d026a64d-5e5d-42f8-8b8b-423a19c7b791",
        "address": "2315 Woodrow St",
        "evidence_url": "https://www.iexitapp.com/Virginia/I-264/Exit%206/76820",
    },
    "68043": {
        "state": "MD",
        "record_id": "ee9f41e7-fb30-4cf3-b19b-adae70154c56",
        "address": "601 Compass Rd",
        "evidence_url": "https://www.dmv-test-pro.com/gas-prices/maryland/middle-river/94228",
    },
    "70154": {
        "state": "CO",
        "record_id": "3e2a22a3-6ba3-4e70-a3f1-74ba14115b6a",
        "address": "103 Hill Street",
        "evidence_url": "https://cms3.revize.com/revize/kersey/Documents/Government/Agenda%20%26%20Minutes/Kersey%20Town%20Board%20Agenda/2024/April%209%2C%202024%20Agenda%20and%20Board%20Packet.pdf",
    },
    "72082": {
        "state": "IN",
        "record_id": "ceab0629-dd1e-4d8e-82e2-2d3d07fc1592",
        "address": "705 W Market St",
        "evidence_url": "https://www.countrymark.com/about-us/news-releases/countrymark-fuel-stations-to-raise-money-for-local-military-families-through-fueling-freedom-program/",
    },
    "72855": {
        "state": "IL",
        "record_id": "4ae95c4c-78b2-484f-bc4d-3c99ee475e16",
        "address": "25653 W Lockport Rd",
        "evidence_url": "https://www.loc8nearme.com/illinois/plainfield/gas-n-wash-plainfield/2285349/",
    },
    "71957": {
        "state": "MO",
        "record_id": "d5578690-5bfd-4a06-b4a1-25235f41fb51",
        "address": "13348 E State Highway 8",
        "evidence_url": "https://winprizes.molottery.com/article.do?id=1768&method=s",
    },
    "71966": {
        "state": "TN",
        "record_id": "b4147c33-f917-47d5-9ff0-892c3579998a",
        "address": "101 Unicoi Village Pl",
        "evidence_url": "https://truckerguideapp.com/diesel-stations/black-diamond-travel-center",
    },
}


def normalized(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def build_catalog(root: Path = ROOT) -> dict:
    fallback_path = root / CSV_COORDINATES
    fallback = json.loads(fallback_path.read_text())
    by_id = {item["opis_id"]: item for item in fallback["records"]}
    exit_path = root / EXIT_CATALOG
    exit_ids = {
        item["opis_id"] for item in json.loads(exit_path.read_text())["records"]
    }
    if set(REVIEWS) - set(by_id) or set(REVIEWS) & exit_ids:
        raise ValueError("reviewed city fuel site is not a city fallback")
    records = []
    source_hashes = {}
    for opis_id, review in REVIEWS.items():
        csv_station = by_id[opis_id]
        mapped_name = review.get("map_name", csv_station["name"])
        path_name = f"var/overture_states/{review['state'].lower()}_places.ndjson"
        path = root / path_name
        source_hashes[path_name] = sha256(path)
        same_name_city = []
        for line in path.open(encoding="utf-8"):
            place = json.loads(line)
            if normalized(place["name"]) != normalized(mapped_name):
                continue
            if any(
                address.get("region") == review["state"]
                and normalized(address.get("locality") or "")
                == normalized(csv_station["city"])
                for address in place["addresses"] or []
            ):
                same_name_city.append(place)
        if len(same_name_city) != 1:
            raise ValueError(f"mapped same-name city fuel place not unique: {opis_id}")
        place = same_name_city[0]
        if (
            place["id"] != review["record_id"]
            or place["basic_category"]
            not in {"gas_station", "truck_stop", "convenience_store", "fueling_station"}
            or place["operating_status"] not in (None, "open")
            or not any(
                address.get("freeform") == review["address"]
                and address.get("region") == csv_station["state"]
                for address in place["addresses"] or []
            )
        ):
            raise ValueError(f"reviewed mapped fuel identity differs: {opis_id}")
        point = re.fullmatch(
            r"POINT \((-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)\)",
            place["point_wkt"],
        )
        if point is None:
            raise ValueError(f"reviewed mapped fuel point differs: {opis_id}")
        longitude, latitude = float(point.group(1)), float(point.group(2))
        distance = distance_miles(
            csv_station["latitude"], csv_station["longitude"], latitude, longitude
        )
        if distance > 3:
            raise ValueError(f"mapped fuel place too far from CSV city: {opis_id}")
        records.append(
            {
                "opis_id": opis_id,
                "country": "US",
                "site_id": f"overture:{place['id']}",
                "place_name": place["name"],
                "mapped_address": review["address"],
                "evidence_url": review["evidence_url"],
                **(
                    {"corroborating_url": review["corroborating_url"]}
                    if "corroborating_url" in review
                    else {}
                ),
                "latitude": latitude,
                "longitude": longitude,
                "location_precision": "site_candidate",
            }
        )
    return {
        "schema_version": 1,
        "source_sha256": fallback["source_sha256"],
        "csv_coordinate_catalog_sha256": sha256(fallback_path),
        "exit_catalog_sha256": sha256(exit_path),
        "source_hashes": dict(sorted(source_hashes.items())),
        "records": sorted(records, key=lambda item: int(item["opis_id"])),
    }


def validate_catalog(root: Path, source_hash: str) -> dict:
    catalog = json.loads((root / OUTPUT).read_text())
    if catalog["source_sha256"] != source_hash or catalog != build_catalog(root):
        raise ValueError("reviewed city fuel sites differ from pinned evidence")
    return catalog
