"""Rank Overture alternatives for human review, without accepting any."""

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "var/pilot/stations.json"
REGIONAL_IDS = {
    "chicago": {"1653", "63946", "69566", "69596", "69683", "70034", "70666", "71318"},
    "imperial_ca": {
        "10085",
        "66475",
        "67669",
        "70321",
        "70452",
        "71543",
        "72368",
        "73078",
    },
}
FUEL_CATEGORIES = {"gas_station", "convenience_store", "truck_stop"}


def normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def branch_number(value: str) -> str | None:
    match = re.search(r"#\s*(\d+)", value)
    return match.group(1) if match else None


def candidate_rank(station: dict, candidate: dict) -> tuple[float, dict]:
    addresses = candidate["addresses"] or []
    in_state = [
        address
        for address in addresses
        if (address.get("region") or "").upper() == station["state"]
        and (address.get("country") or "").upper() == "US"
    ]
    if not in_state:
        return -1.0, {}
    name = candidate["name"] or ""
    aliases = station["aliases"]
    similarity = max(
        SequenceMatcher(None, normalized(alias), normalized(name)).ratio()
        for alias in aliases
    )
    locality_match = any(
        normalized(address.get("locality") or "") == normalized(station["city"])
        for address in in_state
    )
    branch = next(
        (branch_number(alias) for alias in aliases if branch_number(alias)), None
    )
    branch_match = (
        branch is not None
        and re.search(rf"(?<!\d){re.escape(branch)}(?!\d)", name) is not None
    )
    fuel_category = candidate["basic_category"] in FUEL_CATEGORIES
    score = (
        similarity
        + (0.5 if locality_match else 0.0)
        + (0.25 if branch_match else 0.0)
        + (0.1 if fuel_category else 0.0)
    )
    return score, {
        "overture_id": candidate["id"],
        "name": name,
        "category": candidate["basic_category"],
        "existence_confidence": candidate.get("confidence"),
        "operating_status": candidate.get("operating_status"),
        "addresses": in_state,
        "point_wkt": candidate["point_wkt"],
        "name_similarity": round(similarity, 3),
        "locality_match": locality_match,
        "branch_number_match": branch_match,
        "fuel_category": fuel_category,
        "review_status": "unresolved",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--region", choices=REGIONAL_IDS, default="chicago")
    scope.add_argument("--station-id")
    args = parser.parse_args()
    if args.station_id is not None:
        if not args.station_id.isdecimal():
            raise ValueError("station ID must be numeric")
        selected_ids = {args.station_id}
        extract = ROOT / f"var/pilot/stations/{args.station_id}_overture_places.ndjson"
        output = ROOT / f"var/pilot/stations/{args.station_id}_review.json"
    else:
        selected_ids = REGIONAL_IDS[args.region]
        extract = ROOT / f"var/pilot/{args.region}_overture_places.ndjson"
        output = ROOT / f"var/pilot/{args.region}_review.json"
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    stations = [item for item in sample["stations"] if item["opis_id"] in selected_ids]
    if {item["opis_id"] for item in stations} != selected_ids:
        raise ValueError("requested pilot IDs missing from the 100-ID sample")
    candidates = [
        json.loads(line) for line in extract.read_text(encoding="utf-8").splitlines()
    ]
    review = []
    for station in sorted(stations, key=lambda item: int(item["opis_id"])):
        ranked = [candidate_rank(station, item) for item in candidates]
        shortlist = [
            item
            for score, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)
            if score >= 0.65
            and (
                item["name_similarity"] >= 0.65
                or item["branch_number_match"]
                or (item["locality_match"] and item["name_similarity"] >= 0.5)
            )
        ][:8]
        review.append(
            {
                "opis_id": station["opis_id"],
                "source_name": station["name"],
                "source_aliases": station["aliases"],
                "source_address": station["address"],
                "source_city": station["city"],
                "source_state": station["state"],
                "candidates": shortlist,
                "review_status": "unresolved",
                "accepted_overture_id": None,
                "note": (
                    "Ranking is exploratory; independent site and entrance "
                    "evidence is required."
                ),
            }
        )
    output.write_text(
        json.dumps({"release": "2026-09-23.1", "stations": review}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(review)} unresolved station shortlists to {output}")
    for item in review:
        print(item["opis_id"], len(item["candidates"]), "candidate alternatives")


if __name__ == "__main__":
    main()
