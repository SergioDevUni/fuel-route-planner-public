"""Shared canadian pilot sites rules used by review and preparation tools."""

import csv
import hashlib
import json
import math
import re
from collections import defaultdict

from app.stations.catalogs.paths import OVERTURE_RELEASE as RELEASE
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE

EVIDENCE = ROOT / "data/review/canadian_pilot_sites.json"

CATALOG = ROOT / "var/overture_canada"

EXPECTED_BRANCHES = {
    "66418": "818",
    "66988": "821",
    "67031": "798",
    "69032": "866",
    "69586": "853",
    "70332": "1201",
}


def distance_meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    squared = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 12742000 * math.asin(min(1, math.sqrt(squared)))


def validate() -> dict:
    document = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    records = document["records"]
    if len(records) != len(EXPECTED_BRANCHES) or {
        item["opis_id"] for item in records
    } != set(EXPECTED_BRANCHES):
        raise ValueError("Canadian Pilot review identities changed")
    source = defaultdict(list)
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            source[row["OPIS Truckstop ID"].strip()].append(row)
    provinces = {item["province"] for item in records}
    places = {}
    hashes = {}
    for province in provinces:
        path = CATALOG / f"{province.lower()}_places.ndjson"
        data = path.read_bytes()
        manifest = json.loads(
            (CATALOG / f"{province.lower()}_manifest.json").read_text()
        )
        digest = hashlib.sha256(data).hexdigest()
        if (
            manifest["province"] != province
            or manifest["release"] != RELEASE
            or manifest["output_sha256"] != digest
        ):
            raise ValueError(f"Canadian Overture extract changed: {province}")
        hashes[province] = digest
        places.update(
            (place["id"], place) for place in map(json.loads, data.splitlines())
        )
    accepted = []
    for item in records:
        opis_id = item["opis_id"]
        province = item["province"]
        branch = item["branch"]
        place_id = item["overture_id"]
        rows = source[opis_id]
        if (
            branch != EXPECTED_BRANCHES[opis_id]
            or not rows
            or any(row["State"].strip().upper() != province for row in rows)
            or not any(
                re.search(rf"#\s*{re.escape(branch)}\b", row["Truckstop Name"])
                for row in rows
            )
            or not item["official_url"].startswith(
                f"https://locations.pilotflyingj.com/ca/{province.lower()}/"
            )
            or place_id not in places
        ):
            raise ValueError(f"Canadian Pilot identity changed: {opis_id}")
        place = places[place_id]
        match = re.fullmatch(
            r"POINT \((-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)\)", place["point_wkt"]
        )
        if not match or not any(
            address.get("country") == "CA" and address.get("region") == province
            for address in place["addresses"] or []
        ):
            raise ValueError(f"Canadian Pilot point invalid: {opis_id}")
        longitude, latitude = map(float, match.groups())
        if (
            distance_meters(
                (latitude, longitude),
                (item["official_latitude"], item["official_longitude"]),
            )
            > 100
        ):
            raise ValueError(
                f"Canadian Pilot public and Overture points differ: {opis_id}"
            )
        accepted.append(
            {
                "opis_id": opis_id,
                "site_id": f"overture:{place_id}",
                "overture_id": place_id,
                "latitude": latitude,
                "longitude": longitude,
                "site_status": "verified",
                "routing_status": "pending_check",
                "official_url": item["official_url"],
                "official_address": item["official_address"],
            }
        )
    return {
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "evidence_sha256": hashlib.sha256(EVIDENCE.read_bytes()).hexdigest(),
        "overture_hashes": hashes,
        "verified_count": len(accepted),
        "records": accepted,
    }
