"""Apply owner-confirmed site precision to an unchanged coordinate snapshot."""

import hashlib
import json
from pathlib import Path

REVIEW_PATH = "data/review/us_station_manual_review.json"
SITE_PRECISION = "fuel_station_site"


def apply_manual_review(
    root: Path, source_hash: str, records: list[dict]
) -> dict | None:
    path = root / REVIEW_PATH
    if not path.is_file():
        return None
    raw = path.read_bytes()
    review = json.loads(raw)
    if (
        review["schema"] != 1
        or review["source_sha256"] != source_hash
        or review["basis"] != "project_owner_confirmation"
    ):
        raise ValueError("Manual station review differs from the source dataset")
    approved = {item["station_id"]: item for item in review["stations"]}
    if len(approved) != len(review["stations"]) or set(approved) != {
        item["station_id"] for item in records
    }:
        raise ValueError("Manual review must cover each US offer exactly once")
    for item in records:
        verified = approved[item["station_id"]]
        if (item["latitude"], item["longitude"]) != (
            verified["latitude"],
            verified["longitude"],
        ):
            raise ValueError("Station coordinates changed after the manual review")
    for item in records:
        verified = approved[item["station_id"]]
        item["source_location_precision"] = verified["source_location_precision"]
        item["location_precision"] = SITE_PRECISION
        item["location_review_basis"] = review["basis"]
    return {
        "path": REVIEW_PATH,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "basis": review["basis"],
        "confirmed_date": review["confirmed_date"],
        "station_count": len(approved),
    }
