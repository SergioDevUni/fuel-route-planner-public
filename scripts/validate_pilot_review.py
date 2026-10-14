"""Validate regional site evidence against frozen pilot input and Overture POIs."""

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from shapely import wkt

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "var/pilot/stations.json"
REGIONS = ("chicago", "imperial_ca")


def normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", choices=REGIONS, default="chicago")
    args = parser.parse_args()
    evidence_path = ROOT / f"data/review/{args.region}_site_evidence.json"
    extract_path = ROOT / f"var/pilot/{args.region}_overture_places.ndjson"
    manifest_path = ROOT / f"var/pilot/{args.region}_overture_manifest.json"
    output_path = ROOT / f"var/pilot/{args.region}_site_verified.json"
    evidence_bytes = evidence_path.read_bytes()
    evidence = json.loads(evidence_bytes)
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if evidence["source_release"] != manifest["release"]:
        raise ValueError("review and Overture release differ")
    if (
        hashlib.sha256(extract_path.read_bytes()).hexdigest()
        != manifest["output_sha256"]
    ):
        raise ValueError("Overture pilot extract hash mismatch")
    stations = {item["opis_id"]: item for item in sample["stations"]}
    places = {
        item["id"]: item
        for line in extract_path.read_text(encoding="utf-8").splitlines()
        for item in [json.loads(line)]
    }
    records = evidence["records"]
    if len(records) != 8 or len({item["opis_id"] for item in records}) != 8:
        raise ValueError("expected eight distinct reviewed station IDs")
    verified = []
    for item in records:
        station = stations[item["opis_id"]]
        if item["status"] not in {
            "site_verified",
            "review",
            "source_missing",
            "operator_coordinate",
        }:
            raise ValueError("invalid review status")
        if not item["evidence_url"].startswith("https://"):
            raise ValueError("review evidence needs an HTTPS source")
        if any(
            not url.startswith("https://")
            for url in item.get("additional_evidence_urls", [])
        ):
            raise ValueError("additional review evidence needs an HTTPS source")
        if item["status"] in {"source_missing", "operator_coordinate"}:
            if item["overture_id"] is not None:
                raise ValueError("station without Overture POI cannot have its ID")
            if item["status"] == "operator_coordinate":
                if f"#{item['store_number']}" not in station["name"]:
                    raise ValueError("operator store number differs from source")
                latitude = item["latitude"]
                longitude = item["longitude"]
                if (
                    type(latitude) not in (int, float)
                    or type(longitude) not in (int, float)
                    or not math.isfinite(latitude)
                    or not math.isfinite(longitude)
                    or not -90 <= latitude <= 90
                    or not -180 <= longitude <= 180
                    or item["coordinate_field"]
                    != "script[type=application/ld+json].geo"
                ):
                    raise ValueError("invalid operator coordinate evidence")
                verified.append(
                    {
                        "opis_id": item["opis_id"],
                        "source_name": station["name"],
                        "source_address": station["address"],
                        "source_city": station["city"],
                        "overture_id": None,
                        "coordinate_source": "operator_jsonld",
                        "latitude": latitude,
                        "longitude": longitude,
                        "site_status": "verified",
                        "routing_status": "pending_check",
                        "evidence_url": item["evidence_url"],
                        "evidence": item["evidence"],
                        "discrepancy": item["discrepancy"],
                    }
                )
            continue
        place = places[item["overture_id"]]
        addresses = place["addresses"] or []
        if not any(
            normalize(address.get("freeform") or "")
            == normalize(item["expected_address"])
            and (address.get("region") or "").upper() == station["state"]
            and (address.get("country") or "").upper() == "US"
            for address in addresses
        ):
            raise ValueError(f"review address disagrees with POI for {item['opis_id']}")
        point = wkt.loads(place["point_wkt"])
        if point.geom_type != "Point" or not (
            -90 <= point.y <= 90 and -180 <= point.x <= 180
        ):
            raise ValueError("expected valid POI point")
        if item["status"] == "site_verified":
            verified.append(
                {
                    "opis_id": item["opis_id"],
                    "source_name": station["name"],
                    "source_address": station["address"],
                    "source_city": station["city"],
                    "overture_id": item["overture_id"],
                    "coordinate_source": "overture_poi",
                    "overture_name": place["name"],
                    "overture_category": place["basic_category"],
                    "latitude": point.y,
                    "longitude": point.x,
                    "site_status": "verified",
                    "routing_status": "pending_check",
                    "evidence_url": item["evidence_url"],
                    "additional_evidence_urls": item.get(
                        "additional_evidence_urls", []
                    ),
                    "evidence": item["evidence"],
                    "discrepancy": item["discrepancy"],
                    "overture_sources": place["sources"],
                }
            )
    counts = Counter(item["status"] for item in records)
    result = {
        "source_sha256": sample["source_sha256"],
        "review_evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "overture_release": manifest["release"],
        "overture_extract_sha256": manifest["output_sha256"],
        "site_verified_count": len(verified),
        "review_status_counts": dict(sorted(counts.items())),
        "routing_verified_count": 0,
        "stations": verified,
    }
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        f"Validated {len(records)} reviews: {dict(counts)}; no routing points published"
    )


if __name__ == "__main__":
    main()
