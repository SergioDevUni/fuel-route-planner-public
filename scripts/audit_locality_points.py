"""Audit every currently linked locality point against CSV and pinned places.

This internal audit compares source coordinates and prepared place records.
"""

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from shapely.geometry import Point, shape

from app.stations.catalogs.csv_coordinate_catalog import validate_catalog
from app.stations.catalogs.paths import ROOT

ACCOUNTING = "var/station_locations/accounting.ndjson"
CSV_COORDINATES = "var/station_locations/csv_coordinate_catalog.json"
CANDIDATES = "var/station_candidates/candidates.json"
MANIFEST = "var/station_candidates/manifest.json"
STATES = "data/prepared/locations/states_detailed.geojson"
FUEL_CATEGORIES = {"gas_station", "convenience_store", "truck_stop", "fueling_station"}


def normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def branch(value: str) -> str | None:
    matches = re.findall(r"#\s*(\d{2,7})\b", value)
    return matches[0] if len(matches) == 1 else None


def _local_places(root: Path, targets: list[dict]) -> dict[str, list[dict]]:
    by_state: dict[str, list[dict]] = defaultdict(list)
    for item in targets:
        if item["country"] == "US":
            by_state[item["state"]].append(item)
    leads: dict[str, list[dict]] = defaultdict(list)
    for state, stations in by_state.items():
        path = root / f"var/overture_states/{state.lower()}_places.ndjson"
        if not path.exists():
            continue
        by_city: dict[str, list[dict]] = defaultdict(list)
        for station in stations:
            by_city[normalized(station["city"])].append(station)
        for line in path.open(encoding="utf-8"):
            place = json.loads(line)
            if place["basic_category"] not in FUEL_CATEGORIES or place[
                "operating_status"
            ] not in (None, "open"):
                continue
            for address in place["addresses"] or []:
                city = normalized(address.get("locality") or "")
                if (
                    address.get("region") != state
                    or not address.get("freeform")
                    or city not in by_city
                ):
                    continue
                for station in by_city[city]:
                    station_branch = branch(station["name"])
                    place_branch = branch(place["name"])
                    exact = normalized(station["name"]) == normalized(place["name"])
                    numbered = (
                        station_branch is not None
                        and station_branch == place_branch
                        and normalized(station["name"]).split()[0]
                        in normalized(place["name"]).split()
                    )
                    if exact or numbered:
                        leads[station["opis_id"]].append(
                            {
                                "kind": "numbered_branch" if numbered else "exact_name",
                                "place_id": place["id"],
                                "place_name": place["name"],
                                "address": address["freeform"],
                            }
                        )
                break
    return leads


def build_audit(root: Path = ROOT) -> tuple[list[dict], dict]:
    manifest = json.loads((root / MANIFEST).read_text())
    candidate_bytes = (root / CANDIDATES).read_bytes()
    if hashlib.sha256(candidate_bytes).hexdigest() != manifest["candidate_sha256"]:
        raise ValueError("candidate snapshot hash differs")
    candidates = {item["station_id"]: item for item in json.loads(candidate_bytes)}
    csv_catalog = json.loads((root / CSV_COORDINATES).read_text())
    if csv_catalog["source_sha256"] != manifest["source_sha256"]:
        raise ValueError("CSV coordinate source differs from snapshot")
    validate_catalog(root, manifest["source_sha256"])
    fallback = {item["opis_id"]: item for item in csv_catalog["records"]}
    boundaries = {
        item["code"]: shape(item["geometry"])
        for item in json.loads((root / STATES).read_text())["states"]
    }
    accounting = [
        json.loads(line)
        for line in (root / ACCOUNTING).read_text(encoding="utf-8").splitlines()
    ]
    targets = [
        fallback[item["opis_id"]]
        for item in accounting
        if item["review_status"] == "csv_location_coordinate"
    ]
    if len(targets) != manifest["city_representative_station_ids"]:
        raise ValueError("accounting and snapshot locality counts differ")
    accounting_by_id = {item["opis_id"]: item for item in accounting}
    leads = _local_places(root, targets)
    records = []
    for station in targets:
        opis_id = station["opis_id"]
        candidate = candidates.get(opis_id)
        is_us = station["country"] == "US"
        if (
            station["location_precision"] != "city_representative"
            or not station["address"].strip()
            or not station["name"].strip()
            or not station["city"].strip()
            or not station["site_id"].startswith("geonames:")
            or (candidate is None) == is_us
        ):
            raise ValueError(f"locality source/route eligibility differs: {opis_id}")
        if is_us:
            if (
                candidate["name"] not in accounting_by_id[opis_id]["csv_station_names"]
                or candidate["address"] != station["address"]
                or candidate["city"] != station["city"]
                or candidate["state"] != station["state"]
                or candidate["latitude"] != station["latitude"]
                or candidate["longitude"] != station["longitude"]
                or candidate["location_precision"] != "city_representative"
                or not boundaries[station["state"]].covers(
                    Point(station["longitude"], station["latitude"])
                )
            ):
                raise ValueError(f"locality snapshot or boundary differs: {opis_id}")
        possible = leads.get(opis_id, [])
        records.append(
            {
                "opis_id": opis_id,
                "name": station["name"],
                "address": station["address"],
                "city": station["city"],
                "state": station["state"],
                "country": station["country"],
                "site_id": station["site_id"],
                "latitude": station["latitude"],
                "longitude": station["longitude"],
                "route_eligible": is_us,
                "has_exit_or_mile_marker": bool(
                    re.search(
                        r"\bEXIT\b|\bEX\s*\d|\bMM\s*\d|MILE MARKER",
                        station["address"],
                        re.I,
                    )
                ),
                "has_numbered_branch": branch(station["name"]) is not None,
                "same_city_place_leads": possible,
                "review_result": "csv_link_and_coordinate_valid",
                "access_result": "review_if_selected",
            }
        )
    records.sort(key=lambda item: int(item["opis_id"]))
    counts = Counter()
    for item in records:
        counts["total"] += 1
        counts["us_route_eligible" if item["route_eligible"] else "canada_linked"] += 1
        if item["has_exit_or_mile_marker"]:
            counts["exit_or_mile_marker_clue"] += 1
        if item["has_numbered_branch"]:
            counts["numbered_branch_clue"] += 1
        if len(item["same_city_place_leads"]) == 1:
            counts["one_same_city_place_lead"] += 1
        elif len(item["same_city_place_leads"]) > 1:
            counts["multiple_same_city_place_leads"] += 1
    summary = {
        "source_sha256": manifest["source_sha256"],
        "candidate_sha256": manifest["candidate_sha256"],
        "counts": dict(counts),
        "meaning": (
            "CSV link and coordinate integrity checked; pump access remains "
            "pending_check until selected-stop review"
        ),
    }
    return records, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    rows, summary = build_audit()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in rows),
        encoding="utf-8",
    )
    args.report.with_suffix(".summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
