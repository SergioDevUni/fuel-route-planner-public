"""Shared reviewed branch sites rules used by review and preparation tools."""

import json
import re
from pathlib import Path

from app.stations.catalogs.exit_coordinate_catalog import distance_miles
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.files import sha256 as sha256

CSV_COORDINATES = "var/station_locations/csv_coordinate_catalog.json"

OUTPUT = "var/station_locations/reviewed_branch_sites.json"

REVIEWS = {
    "66440": {
        "branch": 4701,
        "source": "var/overture_states/il_places.ndjson",
        "record_id": "59c8d91a-5301-4f6f-aa73-91c04e5f8f7f",
        "name": "Graham Enterprise, Inc.",
        "address": "4701 S Central Ave",
        "state": "IL",
        "evidence_url": "https://allstays.com/truckstops-details/90752.php",
    },
    "63959": {
        "branch": 1429,
        "source": "var/overture_states/il_places.ndjson",
        "record_id": "cf20a17c-121e-4b6e-b9fc-9400d6bc9ddf",
        "name": "Speedway",
        "address": "3S405 State Route 59",
        "state": "IL",
        "evidence_url": "https://warrenville.il.us/BusinessDirectoryii.aspx?lngBusinessCategoryID=0&lngNewPage=1&txtAreaCode=&txtBusinessName=&txtCity=&txtCustomField1=&txtCustomField2=&txtCustomField3=&txtCustomField4=&txtLetter=&txtState=&txtZipCode=&ysnShowAll=1",
    },
    "66461": {
        "branch": 1167,
        "source": "var/overture_states/tx_places.ndjson",
        "record_id": "013fc70b-b832-4622-93ce-bdad116eb27e",
        "name": "Yesway",
        "address": "110 Arnett St",
        "state": "TX",
        "evidence_url": "https://ftp.txdot.gov/pub/txdot/move-texas-freight/studies/truck-parking/3e-truck-parking-in-dedicated-and-unauthorized-locations-combined-appendices.pdf",
        "evidence_latitude": 33.4093,
        "evidence_longitude": -102.16,
    },
    "68029": {
        "branch": 3502,
        "source": "var/overture_states/ga_places.ndjson",
        "record_id": "95430bcb-8b5f-4e86-a2e2-791949d98436",
        "name": "MAPCO",
        "address": "6672 Alabama Hwy",
        "state": "GA",
        "evidence_url": "https://www.gacoam.com/API/Documents/Document?documentID=825",
    },
    "3230": {
        "branch": 40815,
        "source": "var/epa_ust/tx_facilities.ndjson",
        "record_id": "TX118503",
        "name": "7-ELEVEN STORE 40815",
        "address": "112 N US HIGHWAY 281",
        "state": "TX",
        "evidence_url": "https://records.tceq.texas.gov/cs/idcplg?IdcService=GET_FILE&allowInterrupt=1&dDocName=7640533&dID=8724107",
    },
    "66186": {
        "branch": 40813,
        "source": "var/epa_ust/tx_facilities.ndjson",
        "record_id": "TX115527",
        "name": "7-ELEVEN STORE 40813",
        "address": "500 E RICE ST",
        "state": "TX",
        "evidence_url": "https://records.tceq.texas.gov/cs/idcplg?IdcService=GET_FILE&allowInterrupt=1&dDocName=7640522&dID=8724086",
    },
    "6954": {
        "branch": 198,
        "source": "var/epa_ust/nj_facilities.ndjson",
        "record_id": "NJ003507",
        "name": "PAKS FAST SERVICE",
        "address": "198 RT 17 S",
        "state": "NJ",
        "evidence_url": "https://fuelrite.com/fuelrite-nj-locations",
    },
    "71227": {
        "branch": 529,
        "source": "var/overture_states/tx_places.ndjson",
        "record_id": "f1882690-7d38-428f-a246-8ff80e7d1583",
        "name": "Tex Best Travel Center #529",
        "address": "19280 TX-16",
        "state": "TX",
        "evidence_url": "https://www.texbeststores.com/texbest",
    },
    "67750": {
        "branch": 40729,
        "source": "var/overture_states/tx_places.ndjson",
        "record_id": "267be097-7b16-4957-9613-579c58ebefb1",
        "name": "Stripes",
        "address": "2305 Fm 511",
        "state": "TX",
        "evidence_url": "https://www.7-eleven.com/locations/tx/brownsville/2305-fm-511-40729",
    },
    "72350": {
        "branch": 34709,
        "source": "var/overture_states/il_places.ndjson",
        "record_id": "1445e8df-20f8-4d99-a0f7-f70b6cd8e108",
        "name": "Exxon & Mobil Stations",
        "address": "504 N Crego Rd",
        "state": "IL",
        "evidence_url": "https://www.exxonmobilfuels.com/en/find-gas-station/mobil-dekalb-il-7eleven34709-200309329",
    },
    "70255": {
        "branch": 152,
        "source": "var/overture_states/mi_places.ndjson",
        "record_id": "74d2b00d-dd48-45f6-a9c8-a5e5f5e4d7b7",
        "name": "JHFS #152 - CADILLAC JUNCTION",
        "address": "2560 E M-55",
        "state": "MI",
        "evidence_url": "https://www.michigan.gov/-/media/Project/Websites/mdhhs/Folder50/Folder13/Active_Food_Licenses_120221.pdf?rev=439dda5faa97472fbacb34c2b75c0f87",
    },
    "71769": {
        "branch": 60,
        "source": "var/overture_states/nc_places.ndjson",
        "record_id": "7c1f2180-7e0a-4f76-ac60-7b255f8712d1",
        "name": "Duck Thru #60",
        "address": "1100 S. NC 581",
        "state": "NC",
        "evidence_url": "https://duckthru.com/wp-content/uploads/2024/10/Store-List.pdf",
    },
}


def branch(name: str) -> int | None:
    matches = re.findall(r"#\s*(\d{1,7})\b", name)
    return int(matches[0]) if len(matches) == 1 else None


def build_catalog(root: Path = ROOT) -> dict:
    csv_path = root / CSV_COORDINATES
    fallback = json.loads(csv_path.read_text())
    by_id = {item["opis_id"]: item for item in fallback["records"]}
    if set(REVIEWS) - set(by_id):
        raise ValueError("reviewed branch missing from CSV fallback")
    source_hashes = {}
    records = []
    for opis_id, review in REVIEWS.items():
        csv_station = by_id[opis_id]
        if csv_station["state"] != review["state"]:
            raise ValueError(f"CSV branch state differs: {opis_id}")
        csv_branch = branch(csv_station["name"])
        if csv_branch != review["branch"]:
            raise ValueError(f"CSV branch number differs: {opis_id}")
        mapped_branch = branch(review["name"])
        if mapped_branch is not None and mapped_branch != csv_branch:
            raise ValueError(f"mapped branch number differs: {opis_id}")
        if opis_id not in {
            "63959",
            "66440",
            "66461",
            "67750",
            "68029",
            "6954",
            "72350",
        } and not re.search(rf"\b0*{csv_branch}\b", review["name"]):
            raise ValueError(f"mapped branch number missing: {opis_id}")
        if opis_id == "66440" and (
            csv_station["name"] != "BP CONNECT #4701"
            or review["address"] != "4701 S Central Ave"
        ):
            raise ValueError("BP Connect branch address differs")
        if (
            opis_id == "72350"
            and csv_station["address"].upper() != review["address"].upper()
        ):
            raise ValueError(f"CSV branch address differs: {opis_id}")
        path = root / review["source"]
        source_hashes[review["source"]] = sha256(path)
        id_key = "id" if "overture_states" in review["source"] else "Facility_ID"
        matches = [
            item
            for line in path.open(encoding="utf-8")
            if (item := json.loads(line))[id_key] == review["record_id"]
        ]
        if len(matches) != 1:
            raise ValueError(f"reviewed branch map point not unique: {opis_id}")
        mapped = matches[0]
        if id_key == "id":
            if (
                mapped["name"] != review["name"]
                or mapped["basic_category"]
                not in {"gas_station", "convenience_store", "truck_stop"}
                or mapped["operating_status"] not in (None, "open")
                or not any(
                    address.get("freeform") == review["address"]
                    for address in mapped["addresses"] or []
                )
            ):
                raise ValueError(f"reviewed branch place differs: {opis_id}")
            point = re.fullmatch(
                r"POINT \((-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)\)",
                mapped["point_wkt"],
            )
            if point is None:
                raise ValueError(f"reviewed branch point differs: {opis_id}")
            longitude, latitude = float(point.group(1)), float(point.group(2))
            site_id = f"overture:{review['record_id']}"
        else:
            if (
                mapped["Name"] != review["name"]
                or mapped["Address"] != review["address"]
                or mapped["Facility_Status"] != "Open UST(s)"
                or mapped["Address_Match_Type"] != "PointAddress"
            ):
                raise ValueError(f"reviewed branch fuel facility differs: {opis_id}")
            latitude, longitude = mapped["Latitude"], mapped["Longitude"]
            site_id = f"epa-ust:{review['record_id']}"
        distance = distance_miles(
            csv_station["latitude"], csv_station["longitude"], latitude, longitude
        )
        if distance > 10:
            raise ValueError(f"reviewed branch point too far from CSV city: {opis_id}")
        if (
            "evidence_latitude" in review
            and distance_miles(
                review["evidence_latitude"],
                review["evidence_longitude"],
                latitude,
                longitude,
            )
            > 0.1
        ):
            raise ValueError(
                f"reviewed branch point differs from published coordinate: {opis_id}"
            )
        records.append(
            {
                "opis_id": opis_id,
                "country": "US",
                "site_id": site_id,
                "mapped_name": review["name"],
                "mapped_address": review["address"],
                "evidence_url": review["evidence_url"],
                "latitude": latitude,
                "longitude": longitude,
                "location_precision": "site_candidate",
            }
        )
    return {
        "schema_version": 1,
        "source_sha256": fallback["source_sha256"],
        "csv_coordinate_catalog_sha256": sha256(csv_path),
        "source_hashes": dict(sorted(source_hashes.items())),
        "records": sorted(records, key=lambda item: int(item["opis_id"])),
    }


def validate_catalog(root: Path, source_hash: str) -> dict:
    catalog = json.loads((root / OUTPUT).read_text())
    if catalog["source_sha256"] != source_hash or catalog != build_catalog(root):
        raise ValueError("reviewed branch sites differ from pinned sources")
    return catalog
