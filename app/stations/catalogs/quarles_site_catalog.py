"""Shared quarles site catalog rules used by review and preparation tools."""

import csv
import hashlib
import json
import math
import re
from collections import defaultdict

from app.stations.catalogs.comdata_matching import (
    FUEL_CATEGORIES,
    address_tokens,
    plain,
    point_coordinates,
)
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE
from app.stations.catalogs.source_queue import build_queue

GUIDE = ROOT / "var/quarles/guide_extract.json"

OVERTURE = ROOT / "var/overture_states"

OUTPUT = ROOT / "var/operator_review/quarles_site_catalog.json"

BRANCH = re.compile(r"\bQUARLES\s*#(\d{2,6})\b", re.I)


def house_number(value: str) -> str | None:
    match = re.match(r"^\s*(\d{1,6})[A-Za-z]?\b", value)
    return match.group(1) if match else None


def guide_address(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+)\s+(\d{5})", value)
    if match is None or house_number(match.group(1)) is None:
        raise ValueError(f"invalid Quarles address: {value}")
    return match.group(1), match.group(2)


def street_tokens(value: str) -> set[str]:
    return {"lane" if token == "ln" else token for token in address_tokens(value)}


def guide_rows() -> dict[str, dict]:
    document = json.loads(GUIDE.read_text(encoding="utf-8"))
    if document["source_url"] != (
        "https://www.quarlesinc.com/wp-content/uploads/2026/04/"
        "QFF-pocket-site-guide-v4-2026.pdf"
    ):
        raise ValueError("unexpected Quarles guide source")
    rows = {}
    for row in document["records"]:
        branch = row["branch"]
        guide_address(row["address"])
        if branch in rows:
            raise ValueError(f"duplicate Quarles guide branch {branch}")
        rows[branch] = row
    return rows


def close_points(places: list[dict], meters: float = 35) -> bool:
    points = [point_coordinates(place["point_wkt"]) for place in places]
    if any(point is None for point in points):
        return False
    for first in points:
        for second in points:
            latitude_delta = math.radians(second[0] - first[0])
            longitude_delta = math.radians(second[1] - first[1])
            cosine = math.cos(math.radians((first[0] + second[0]) / 2))
            distance = 6371000 * math.hypot(latitude_delta, longitude_delta * cosine)
            if distance > meters:
                return False
    return True


def choose_place(guide: dict, state: str, places: list[dict]) -> dict | None:
    street, postcode = guide_address(guide["address"])
    target = street_tokens(street)
    matches = []
    for place in places:
        if (
            place["basic_category"] not in FUEL_CATEGORIES
            or place["operating_status"] not in {None, "open"}
            or point_coordinates(place["point_wkt"]) is None
        ):
            continue
        branded = "quarles" in plain(place["name"] or "")
        if (place["confidence"] or 0) < (0.75 if branded else 0.8):
            continue
        for address in place["addresses"] or []:
            freeform = address.get("freeform") or ""
            candidate = street_tokens(freeform)
            overlap = len(target & candidate) / max(1, len(target | candidate))
            if (
                address.get("region") == state
                and address.get("country") == "US"
                and (address.get("postcode") or "")[:5] == postcode
                and house_number(freeform) == house_number(street)
                and overlap >= 0.6
            ):
                matches.append(place)
                break
    branded_matches = [
        place for place in matches if "quarles" in plain(place["name"] or "")
    ]
    choices = branded_matches or matches
    if len(choices) == 1:
        return choices[0]
    if not choices or not close_points(choices):
        return None
    ranked = sorted(choices, key=lambda place: place["confidence"] or 0, reverse=True)
    return ranked[0] if ranked[0]["confidence"] > ranked[1]["confidence"] else None


def _candidate_stations() -> dict[str, dict]:
    queue = build_queue()
    eligible = {item["opis_id"]: item for item in queue["stations"]}
    if OUTPUT.exists():
        previous = json.loads(OUTPUT.read_text())
        if previous["source_sha256"] != queue["source_sha256"]:
            raise ValueError("previous Quarles catalog belongs to another CSV")
        eligible.update(
            (record["opis_id"], record["csv_station"]) for record in previous["records"]
        )
    return eligible


def build(stations: dict[str, dict] | None = None) -> dict:
    rows = guide_rows()
    stations = _candidate_stations() if stations is None else stations
    by_state = defaultdict(list)
    for station in stations.values():
        if station["country"] != "US" or len(station["states"]) != 1:
            continue
        branches = {
            match.group(1)
            for name in station["names"]
            for match in BRANCH.finditer(name)
        }
        if len(branches) != 1:
            continue
        branch = next(iter(branches))
        if branch not in rows:
            continue
        guide = rows[branch]
        by_state[station["states"][0]].append((station, guide))
    records = []
    hashes = {}
    for state, items in sorted(by_state.items()):
        path = OVERTURE / f"{state.lower()}_places.ndjson"
        manifest = json.loads((OVERTURE / f"{state.lower()}_manifest.json").read_text())
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != manifest["output_sha256"]:
            raise ValueError(f"Overture state extract changed: {state}")
        hashes[state] = digest
        wanted = {
            (
                house_number(guide_address(row["address"])[0]),
                guide_address(row["address"])[1],
            )
            for _, row in items
        }
        index = defaultdict(dict)
        for line in content.splitlines():
            place = json.loads(line)
            for address in place["addresses"] or []:
                key = (
                    house_number(address.get("freeform") or ""),
                    (address.get("postcode") or "")[:5],
                )
                if address.get("region") == state and key in wanted:
                    index[key][place["id"]] = place
        for station, row in items:
            street, postcode = guide_address(row["address"])
            key = house_number(street), postcode
            place = choose_place(row, state, list(index[key].values()))
            if place is None:
                continue
            latitude, longitude = point_coordinates(place["point_wkt"])
            records.append(
                {
                    "opis_id": station["opis_id"],
                    "csv_station": station,
                    "guide_row": row,
                    "overture_id": place["id"],
                    "overture_name": place["name"],
                    "site_id": f"overture:{place['id']}",
                    "latitude": latitude,
                    "longitude": longitude,
                    "city_matches": plain(row["city"])
                    in {plain(city) for city in station["cities"]},
                    "rule": (
                        "quarles_branch_public_street_zip_and_fuel_point"
                        if "quarles" in plain(place["name"] or "")
                        else "quarles_branch_public_street_zip_and_colocated_fuel_point"
                    ),
                }
            )
    records.sort(key=lambda item: int(item["opis_id"]))
    return {
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "guide_sha256": hashlib.sha256(GUIDE.read_bytes()).hexdigest(),
        "overture_state_hashes": hashes,
        "verified_count": len(records),
        "records": records,
    }


def validate_catalog(document: dict) -> None:
    if (
        document["source_sha256"] != hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        or document["guide_sha256"] != hashlib.sha256(GUIDE.read_bytes()).hexdigest()
        or document["verified_count"] != len(document["records"])
    ):
        raise ValueError("Quarles catalog source differs")
    source_groups = defaultdict(list)
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            source_groups[row["OPIS Truckstop ID"].strip()].append(row)
    stations = {}
    for record in document["records"]:
        opis_id = record["opis_id"]
        group = source_groups.get(opis_id, [])
        expected = {
            "opis_id": opis_id,
            "country": "US",
            "states": sorted({row["State"].strip().upper() for row in group}),
            "names": sorted({row["Truckstop Name"].strip() for row in group}),
            "cities": sorted({row["City"].strip() for row in group}),
            "addresses": sorted({row["Address"].strip() for row in group}),
        }
        if (
            not group
            or opis_id in stations
            or any(
                record["csv_station"].get(key) != value
                for key, value in expected.items()
            )
        ):
            raise ValueError(f"Quarles CSV identity changed: {opis_id}")
        stations[opis_id] = record["csv_station"]
    for state, expected_hash in document["overture_state_hashes"].items():
        path = OVERTURE / f"{state.lower()}_places.ndjson"
        manifest = json.loads((OVERTURE / f"{state.lower()}_manifest.json").read_text())
        if (
            hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash
            or manifest["output_sha256"] != expected_hash
        ):
            raise ValueError(f"Quarles Overture evidence changed: {state}")
    rebuilt = build(stations)
    if rebuilt["records"] != document["records"]:
        raise ValueError("Quarles catalog matches no longer reproduce")
