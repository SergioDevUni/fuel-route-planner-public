"""Shared caseys site catalog rules used by review and preparation tools."""

import hashlib
import json
import re
from collections import defaultdict
from urllib.parse import urlparse

from app.stations.catalogs.comdata_matching import (
    address_tokens,
    distance_miles,
    point_coordinates,
)
from app.stations.catalogs.overture_identity import (
    branch_number,
    city_centers,
    nearby_city,
    road_numbers,
    source_stations,
)
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE

URLS = ROOT / "var/operator_review/caseys_official_urls.txt"

OVERTURE = ROOT / "var/overture_states"

CITY_PLACES = ROOT / "data/prepared/locations/places.ndjson"

HOUSE = re.compile(r"^\s*(\d+[a-z]?\d*)\b", re.IGNORECASE)

ADDRESS_STOP = {
    "north",
    "south",
    "east",
    "west",
    "highway",
    "road",
    "street",
    "avenue",
    "drive",
    "route",
    "state",
    "county",
    "us",
    "interstate",
    "il",
    "mn",
    "ct",
    "ln",
}


def official_rows() -> list[tuple[str, str]]:
    rows = [
        tuple(line.split())
        for line in URLS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if any(len(row) != 2 for row in rows) or len({row[0] for row in rows}) != len(rows):
        raise ValueError("invalid Casey URL evidence rows")
    return rows


def parsed_url(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.caseys.com"
        or len(parts) != 4
        or parts[0] != "general-store"
        or not re.fullmatch(r"[a-z]{2}-[a-z0-9]+", parts[1])
        or not re.fullmatch(r"[a-z0-9-]+", parts[2])
        or not re.fullmatch(r"\d{2,6}", parts[3])
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"invalid official Casey URL: {url}")
    return parts[1][:2].upper(), parts[2].replace("-", " "), parts[3]


def road_match(official_street: str, mapped_street: str) -> bool:
    first = HOUSE.match(official_street)
    second = HOUSE.match(mapped_street)
    if (
        first is None
        or second is None
        or first.group(1).lower() != second.group(1).lower()
    ):
        return False
    official_tokens = address_tokens(official_street)
    mapped_tokens = address_tokens(mapped_street)
    if official_tokens == mapped_tokens:
        return True
    meaningful = ((official_tokens & mapped_tokens) - ADDRESS_STOP) - {
        first.group(1).lower()
    }
    meaningful = {token for token in meaningful if not token.isdecimal()}
    first_road = road_numbers(official_street)
    second_road = road_numbers(mapped_street)
    return bool(meaningful or (first_road and first_road & second_road))


def choose_site(
    places: list[dict], state: str, street: str, cities: list[str], centers: dict
) -> tuple[dict, list[str]] | None:
    matched: list[tuple[dict, tuple[float, float]]] = []
    for place in places:
        if (
            not re.search(
                r"\bcasey(?:'s|s)?\b|goodstop", place["name"] or "", re.IGNORECASE
            )
            or (place["confidence"] or 0) < 0.75
            or place["operating_status"] not in {None, "open"}
        ):
            continue
        point = point_coordinates(place["point_wkt"])
        if (
            point is None
            or nearby_city(state, cities, point, centers, max_miles=15) is None
        ):
            continue
        for address in place["addresses"] or []:
            if (
                address.get("region") == state
                and address.get("country") == "US"
                and road_match(street, address.get("freeform") or "")
            ):
                matched.append((place, point))
                break
    if not matched or any(
        distance_miles(matched[0][1], item[1]) > 0.12 for item in matched
    ):
        return None
    matched.sort(key=lambda item: (item[0]["confidence"], item[0]["id"]), reverse=True)
    return matched[0][0], sorted({item[0]["id"] for item in matched})


def build_catalog() -> dict:
    source = source_stations()
    centers = city_centers()
    by_state: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for opis_id, url in official_rows():
        station = source[opis_id]
        state, street, branch = parsed_url(url)
        csv_branches = {branch_number(name) for name in station["names"]}
        if (
            station["states"] != [state]
            or csv_branches != {branch}
            or "casey" not in " ".join(station["names"]).lower()
        ):
            raise ValueError(f"Casey source identity mismatch: {opis_id}")
        by_state[state].append((opis_id, street, url))
    records: list[dict] = []
    state_hashes: dict[str, str] = {}
    for state, pending in sorted(by_state.items()):
        data = (OVERTURE / f"{state.lower()}_places.ndjson").read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        manifest = json.loads(
            (OVERTURE / f"{state.lower()}_manifest.json").read_text(encoding="utf-8")
        )
        if manifest["output_sha256"] != digest:
            raise ValueError(f"Overture state evidence changed: {state}")
        state_hashes[state] = digest
        places = [json.loads(line) for line in data.splitlines()]
        for opis_id, street, url in pending:
            chosen = choose_site(
                places, state, street, source[opis_id]["cities"], centers
            )
            if chosen is None:
                continue
            place, alternatives = chosen
            point = point_coordinates(place["point_wkt"])
            if point is None:
                raise ValueError("chosen Casey point missing")
            records.append(
                {
                    "opis_id": opis_id,
                    "official_url": url,
                    "overture_id": place["id"],
                    "overture_alternatives": alternatives,
                    "site_id": f"overture:{place['id']}",
                    "latitude": point[0],
                    "longitude": point[1],
                }
            )
    records.sort(key=lambda record: int(record["opis_id"]))
    return {
        "status": "official_branch_address_overture_point",
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "official_urls_sha256": hashlib.sha256(URLS.read_bytes()).hexdigest(),
        "city_catalog_sha256": hashlib.sha256(CITY_PLACES.read_bytes()).hexdigest(),
        "overture_state_hashes": state_hashes,
        "searched_count": 59,
        "official_url_count": len(official_rows()),
        "verified_count": len(records),
        "records": records,
    }


def validate_catalog(document: dict) -> None:
    rebuilt = build_catalog()
    if document != rebuilt:
        raise ValueError("Casey catalog differs from pinned evidence")
