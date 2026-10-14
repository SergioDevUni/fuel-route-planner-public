"""Shared quarles epa site catalog rules used by review and preparation tools."""

import hashlib
import json
import re
from collections import defaultdict

from app.stations.catalogs.epa_ust_site_catalog import load_state, usable_coordinate
from app.stations.catalogs.overture_identity import source_stations
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE
from app.stations.catalogs.quarles_site_catalog import (
    BRANCH,
    GUIDE,
    guide_address,
    guide_rows,
    house_number,
    street_tokens,
)
from app.stations.catalogs.source_queue import build_queue

OUTPUT = ROOT / "var/operator_review/quarles_epa_site_catalog.json"

OPERATOR = re.compile(r"\b(?:Q[\s-]*CARD|QFN|QUARLES\s+FOOD\s+STORE)\b", re.I)


def branch_suffix_matches(branch: str, name: str) -> bool:
    if OPERATOR.search(name) is None:
        return False
    return any(
        number.lstrip("0") == branch[-3:].lstrip("0")
        for number in re.findall(r"(?<!\d)\d{1,4}(?!\d)", name)
    )


def matching_facilities(branch: str, guide: dict, facilities: list[dict]) -> list[dict]:
    street, postcode = guide_address(guide["address"])
    target = street_tokens(street)
    matches = []
    for facility in facilities:
        candidate = street_tokens(facility["Address"] or "")
        overlap = len(target & candidate) / max(1, len(target | candidate))
        if (
            (facility["Zip_Code"] or "")[:5] == postcode
            and house_number(facility["Address"] or "") == house_number(street)
            and overlap >= 0.6
            and usable_coordinate(facility)
            and branch_suffix_matches(branch, facility["Name"] or "")
        ):
            matches.append(facility)
    return matches


def eligible_stations() -> dict[str, dict]:
    queue = build_queue()
    source = source_stations()
    ids = {item["opis_id"] for item in queue["stations"]}
    if OUTPUT.exists():
        previous = json.loads(OUTPUT.read_text())
        if previous["source_sha256"] != queue["source_sha256"]:
            raise ValueError("previous Quarles EPA catalog belongs to another CSV")
        ids.update(item["opis_id"] for item in previous["records"])
    return {opis_id: dict(source[opis_id], opis_id=opis_id) for opis_id in ids}


def build(stations: dict[str, dict] | None = None) -> dict:
    stations = eligible_stations() if stations is None else stations
    guide = guide_rows()
    by_state = defaultdict(list)
    for station in stations.values():
        if len(station["states"]) != 1:
            continue
        branches = {
            match.group(1)
            for name in station["names"]
            for match in BRANCH.finditer(name)
        }
        if len(branches) != 1:
            continue
        branch = next(iter(branches))
        if branch in guide:
            by_state[station["states"][0]].append((station, branch, guide[branch]))
    records = []
    hashes = {}
    for state, items in sorted(by_state.items()):
        facilities, digest = load_state(state)
        hashes[state] = digest
        wanted = {
            (
                house_number(guide_address(row["address"])[0]),
                guide_address(row["address"])[1],
            )
            for _, _, row in items
        }
        index = defaultdict(list)
        for facility in facilities:
            key = (
                house_number(facility["Address"] or ""),
                (facility["Zip_Code"] or "")[:5],
            )
            if key in wanted:
                index[key].append(facility)
        for station, branch, row in items:
            street, postcode = guide_address(row["address"])
            matches = matching_facilities(
                branch, row, index[(house_number(street), postcode)]
            )
            if len(matches) != 1:
                continue
            facility = matches[0]
            records.append(
                {
                    "opis_id": station["opis_id"],
                    "state": state,
                    "branch": branch,
                    "guide_row": row,
                    "facility": facility,
                    "site_id": f"epa-ust:{facility['OBJECTID']}",
                    "latitude": facility["Latitude"],
                    "longitude": facility["Longitude"],
                    "rule": "quarles_guide_branch_address_and_public_q_card_facility",
                }
            )
    records.sort(key=lambda record: int(record["opis_id"]))
    return {
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "guide_sha256": hashlib.sha256(GUIDE.read_bytes()).hexdigest(),
        "epa_state_hashes": hashes,
        "verified_count": len(records),
        "records": records,
    }


def validate_catalog(document: dict) -> None:
    if (
        document["source_sha256"] != hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        or document["guide_sha256"] != hashlib.sha256(GUIDE.read_bytes()).hexdigest()
        or document["verified_count"] != len(document["records"])
        or len({item["opis_id"] for item in document["records"]})
        != len(document["records"])
        or len({item["site_id"] for item in document["records"]})
        != len(document["records"])
    ):
        raise ValueError("Quarles EPA catalog identity changed")
    source = source_stations()
    if not set(item["opis_id"] for item in document["records"]) <= set(source):
        raise ValueError("Quarles EPA catalog contains an unknown CSV ID")
    stations = {
        item["opis_id"]: dict(source[item["opis_id"]], opis_id=item["opis_id"])
        for item in document["records"]
    }
    for state, expected_hash in document["epa_state_hashes"].items():
        _, actual_hash = load_state(state)
        if actual_hash != expected_hash:
            raise ValueError(f"Quarles EPA facility extract changed: {state}")
    rebuilt = build(stations)
    if rebuilt["records"] != document["records"] or any(
        rebuilt["epa_state_hashes"].get(state)
        != document["epa_state_hashes"].get(state)
        for state in rebuilt["epa_state_hashes"]
    ):
        raise ValueError("Quarles EPA catalog evidence no longer reproduces")
