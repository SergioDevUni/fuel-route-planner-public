"""Shared source queue rules used by review and preparation tools."""

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict

from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE
from app.stations.catalogs.regions import STATES

PROFILE = ROOT / "data/audit/source-profile.json"

ACCOUNTING = ROOT / "var/station_locations/accounting.ndjson"

ACCOUNTING_MANIFEST = ROOT / "var/station_locations/manifest.json"

SNAPSHOT_MANIFEST = ROOT / "var/station_candidates/manifest.json"

CHAINS = (
    ("petro_canada", re.compile(r"\bPETRO-CANADA\b", re.I)),
    ("pilot_family", re.compile(r"\b(?:PILOT|FLYING\s+J|ONE9)\b", re.I)),
    ("loves", re.compile(r"\bLOVE'?S\b", re.I)),
    ("ta_petro", re.compile(r"^(?:TA\s|PETRO\s+(?:STOPPING\s+CENTER|STOP)\b)", re.I)),
    ("circle_k", re.compile(r"\bCIRCLE\s+K\b", re.I)),
    ("kwik_trip", re.compile(r"\bKWIK\s+(?:TRIP|STAR)\b", re.I)),
    ("speedway", re.compile(r"\bSPEEDWAY\b", re.I)),
    ("caseys", re.compile(r"\bCASEY'?S\b", re.I)),
    ("quiktrip", re.compile(r"\bQUIKTRIP\b", re.I)),
)


def family(names: list[str]) -> str:
    matches = {
        label for name in names for label, pattern in CHAINS if pattern.search(name)
    }
    return next(iter(matches)) if len(matches) == 1 else "mixed" if matches else "other"


def build_queue() -> dict:
    source_bytes = SOURCE.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    profile = json.loads(PROFILE.read_text())
    accounting_bytes = ACCOUNTING.read_bytes()
    accounting_manifest = json.loads(ACCOUNTING_MANIFEST.read_text())
    snapshot = json.loads(SNAPSHOT_MANIFEST.read_text())
    if (
        source_hash != profile["sha256"]
        or source_hash != accounting_manifest["source_sha256"]
        or source_hash != snapshot["source_sha256"]
        or hashlib.sha256(accounting_bytes).hexdigest()
        != accounting_manifest["accounting_sha256"]
        or accounting_manifest["total_csv_station_ids"] != profile["unique_station_ids"]
    ):
        raise ValueError("source, accounting, and candidate snapshot differ")
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            groups[row["OPIS Truckstop ID"].strip()].append(row)
    accounting = {}
    for line in accounting_bytes.splitlines():
        row = json.loads(line)
        if row["opis_id"] in accounting:
            raise ValueError("accounting repeats a CSV ID")
        accounting[row["opis_id"]] = row
    if set(accounting) != set(groups) or len(groups) != profile["unique_station_ids"]:
        raise ValueError("accounting omits source CSV identities")
    missing = []
    for opis_id, rows in sorted(groups.items(), key=lambda item: int(item[0])):
        review = accounting[opis_id]
        if review["identity_status"] in {"verified", "location_estimated"}:
            continue
        names = sorted({row["Truckstop Name"].strip() for row in rows})
        states = sorted({row["State"].strip().upper() for row in rows})
        missing.append(
            {
                "opis_id": opis_id,
                "country": "US" if all(state in STATES for state in states) else "CA",
                "states": states,
                "names": names,
                "cities": sorted({row["City"].strip() for row in rows}),
                "addresses": sorted({row["Address"].strip() for row in rows}),
                "family": family(names),
                "review_status": review["review_status"],
                "reason": review["reason"],
            }
        )
    if (
        len(missing)
        != profile["unique_station_ids"] - snapshot["linked_csv_station_ids"]
    ):
        raise ValueError("unmatched count differs from candidate snapshot")
    return {
        "source_sha256": source_hash,
        "accounting_sha256": hashlib.sha256(accounting_bytes).hexdigest(),
        "candidate_sha256": snapshot["candidate_sha256"],
        "total_csv_station_ids": len(groups),
        "linked_csv_station_ids": snapshot["linked_csv_station_ids"],
        "unmatched_station_ids": len(missing),
        "by_country": dict(
            sorted(Counter(item["country"] for item in missing).items())
        ),
        "by_family": dict(sorted(Counter(item["family"] for item in missing).items())),
        "stations": missing,
    }
