"""Validate Delaware CSV sites against pinned public and Overture evidence."""

import csv
import hashlib
import json
import math
import re
from collections import defaultdict

from pypdf import PdfReader

from app.stations.catalogs.paths import ROOT, SOURCE

REVIEW = ROOT / "data/review/de_public_sites.json"
PDF = ROOT / "var/overture_states/de_truck_flyer_2024.pdf"
CATALOG = ROOT / "var/overture_states/de_places.ndjson"
MANIFEST = ROOT / "var/overture_states/de_manifest.json"
OUTPUT = ROOT / "var/overture_states/de_public_verified.json"


def distance_miles(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    latitude_delta = math.radians(b_lat - a_lat)
    longitude_delta = math.radians(b_lon - a_lon)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(a_lat))
        * math.cos(math.radians(b_lat))
        * math.sin(longitude_delta / 2) ** 2
    )
    return 3958.7613 * 2 * math.asin(math.sqrt(value))


def compact(value: str) -> str:
    return " ".join(value.casefold().split())


def validate() -> dict:
    evidence_bytes = REVIEW.read_bytes()
    evidence = json.loads(evidence_bytes)
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    pdf_bytes = PDF.read_bytes()
    catalog_bytes = CATALOG.read_bytes()
    manifest = json.loads(MANIFEST.read_bytes())
    if (
        evidence["source_sha256"] != source_hash
        or evidence["public_source_sha256"] != hashlib.sha256(pdf_bytes).hexdigest()
        or evidence["overture_catalog_sha256"]
        != hashlib.sha256(catalog_bytes).hexdigest()
        or manifest["output_sha256"] != evidence["overture_catalog_sha256"]
        or manifest["release"] != evidence["overture_release"]
        or manifest["state"] != evidence["state"]
    ):
        raise ValueError("Delaware source, PDF, or candidate catalog changed")
    source: dict[str, list[dict[str, str]]] = defaultdict(list)
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            source[row["OPIS Truckstop ID"].strip()].append(row)
    places = {
        place["id"]: place for place in map(json.loads, catalog_bytes.splitlines())
    }
    pdf_text = "\n".join(page.extract_text() for page in PdfReader(PDF).pages)
    verified = []
    used_ids: set[str] = set()
    for record in evidence["records"]:
        opis_id = record["opis_id"]
        place_id = record["overture_id"]
        rows = source.get(opis_id, [])
        if (
            not rows
            or opis_id in used_ids
            or {row["State"].strip() for row in rows} != {evidence["state"]}
            or {compact(row["Truckstop Name"]) for row in rows}
            != {compact(record["csv_name"])}
            or {compact(row["City"]) for row in rows} != {compact(record["csv_city"])}
            or compact(record["csv_name"]) != compact(record["public_name"])
            or compact(record["csv_city"]) != compact(record["public_city"])
        ):
            raise ValueError(f"CSV identity evidence differs for {opis_id}")
        excerpt = (
            f"{record['public_name']}\n{record['public_address']}\n"
            f"{record['public_city']}, DE {record['public_postcode']}\n"
            f"{record['latitude']:.4f}, {record['longitude']:.4f}"
        )
        if compact(excerpt) not in compact(pdf_text):
            raise ValueError(f"public PDF evidence differs for {opis_id}")
        place = places.get(place_id)
        if place is None or compact(place["name"]) != compact(record["overture_name"]):
            raise ValueError(f"Overture place name differs for {opis_id}")
        addresses = place["addresses"] or []
        if not any(
            compact(address.get("freeform") or "") == compact(record["public_address"])
            and compact(address.get("locality") or "") == compact(record["public_city"])
            and address.get("region") == "DE"
            for address in addresses
        ):
            raise ValueError(f"Overture address differs for {opis_id}")
        coordinates = re.fullmatch(
            r"POINT \((-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)\)",
            place["point_wkt"],
        )
        if (
            coordinates is None
            or distance_miles(
                record["latitude"],
                record["longitude"],
                float(coordinates.group(2)),
                float(coordinates.group(1)),
            )
            > 0.35
        ):
            raise ValueError(f"public and Overture coordinates differ for {opis_id}")
        used_ids.add(opis_id)
        verified.append(
            {
                **record,
                "site_id": f"overture:{place_id}",
                "site_status": "verified",
                "routing_status": "pending_check",
                "country": "US",
            }
        )
    result = {
        "source_sha256": source_hash,
        "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "public_source_sha256": evidence["public_source_sha256"],
        "overture_catalog_sha256": evidence["overture_catalog_sha256"],
        "overture_release": evidence["overture_release"],
        "verified_count": len(verified),
        "records": verified,
    }
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    print(f"{validate()['verified_count']} Delaware sites verified")
