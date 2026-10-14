"""Shared canadian petro sites rules used by review and preparation tools."""

import csv
import hashlib
import json
import re
from collections import defaultdict

from app.stations.catalogs.paths import OVERTURE_RELEASE as RELEASE
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE

EVIDENCE = ROOT / "data/review/canadian_petro_sites.json"

CATALOG = ROOT / "var/overture_canada"

EXPECTED = {
    "65939": ("BC", "Kamloops", {"1", "366"}, "1885"),
    "72213": ("ON", "Cookstown", {"89", "400"}, "3535"),
}


def validate() -> dict:
    evidence_bytes = EVIDENCE.read_bytes()
    records = json.loads(evidence_bytes)["records"]
    if len(records) != len(EXPECTED) or {row["opis_id"] for row in records} != set(
        EXPECTED
    ):
        raise ValueError("Canadian Petro review identities changed")
    source = defaultdict(list)
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            source[row["OPIS Truckstop ID"].strip()].append(row)
    places = {}
    hashes = {}
    for province in {record["province"] for record in records}:
        data = (CATALOG / f"{province.lower()}_places.ndjson").read_bytes()
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
    for record in records:
        opis_id = record["opis_id"]
        province, city, roads, street_number = EXPECTED[opis_id]
        rows = source[opis_id]
        place_id = record["overture_id"]
        place = places.get(place_id)
        if (
            record["province"] != province
            or not rows
            or any(
                row["State"].strip().upper() != province
                or row["City"].strip().casefold() != city.casefold()
                or "PETRO-CANADA" not in row["Truckstop Name"].upper()
                for row in rows
            )
            or not any(roads <= set(re.findall(r"\d+", row["Address"])) for row in rows)
            or not record["official_url"].startswith("https://www.petro-canada.ca/")
            or street_number not in record["official_address"]
            or place is None
            or not (place["name"] or "").casefold().startswith("petro")
        ):
            raise ValueError(f"Canadian Petro review identity changed: {opis_id}")
        addresses = place["addresses"] or []
        if not any(
            address.get("country") == "CA"
            and address.get("region") == province
            and street_number in (address.get("freeform") or "")
            for address in addresses
        ):
            raise ValueError(f"Canadian Petro physical address changed: {opis_id}")
        point = re.fullmatch(
            r"POINT \((-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)\)", place["point_wkt"]
        )
        if not point:
            raise ValueError(f"Canadian Petro point invalid: {opis_id}")
        longitude, latitude = map(float, point.groups())
        accepted.append(
            {
                "opis_id": opis_id,
                "site_id": f"overture:{place_id}",
                "overture_id": place_id,
                "latitude": latitude,
                "longitude": longitude,
                "site_status": "verified",
                "routing_status": "pending_check",
                "official_url": record["official_url"],
                "official_address": record["official_address"],
            }
        )
    return {
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "overture_hashes": hashes,
        "verified_count": len(accepted),
        "records": accepted,
    }
