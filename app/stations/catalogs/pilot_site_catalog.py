"""Shared pilot site catalog rules used by review and preparation tools."""

import csv
import re
from collections import defaultdict
from pathlib import Path

from app.domain.text import canonical_text
from app.stations.catalogs.regions import STATES

FAMILY = re.compile(r"\b(PILOT|FLYING\s+J|ONE9)\b", re.IGNORECASE)

BRANCH = re.compile(r"#\s*(\d+)\b")


def brand(value: str) -> str | None:
    match = FAMILY.search(value)
    if match is None:
        return None
    text = match.group(1).upper().replace(" ", "")
    return "flying_j" if text == "FLYINGJ" else text.lower()


def family_branch(value: str) -> int | None:
    family = FAMILY.search(value)
    if family is None:
        return None
    following = value[family.end() :]
    match = BRANCH.search(following[:45])
    return int(match.group(1)) if match else None


def source_branches(source: Path) -> dict[str, dict]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    with source.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if brand(row["Truckstop Name"]):
                groups[row["OPIS Truckstop ID"].strip()].append(row)
    result = {}
    for opis_id, rows in groups.items():
        result[opis_id] = {
            "opis_id": opis_id,
            "brands": sorted({brand(row["Truckstop Name"]) for row in rows}),
            "branches": sorted(
                {
                    number
                    for row in rows
                    if (number := family_branch(row["Truckstop Name"])) is not None
                }
            ),
            "cities": sorted({canonical_text(row["City"]) for row in rows}),
            "regions": sorted({row["State"].strip().upper() for row in rows}),
            "source_names": sorted({row["Truckstop Name"].strip() for row in rows}),
        }
    return result


def reconcile(
    source: dict[str, dict], pages: list[dict]
) -> tuple[list[dict], list[dict]]:
    by_key: dict[tuple[str, int, str], list[dict]] = defaultdict(list)
    for page in pages:
        by_key[(page["brand"], page["store_number"], page["state"])].append(page)
    linked = []
    unresolved = []
    proposed: dict[str, dict] = {}
    for opis_id, row in sorted(source.items(), key=lambda item: int(item[0])):
        reason = None
        if (
            len(row["brands"]) != 1
            or len(row["branches"]) != 1
            or len(row["regions"]) != 1
        ):
            reason = "source_branch_or_brand_ambiguous"
            candidates = []
        else:
            candidates = by_key.get(
                (row["brands"][0], row["branches"][0], row["regions"][0]), []
            )
            # The CSV's exit locality can differ from the operator's mailing
            # city. The unique brand/store-number/state key identifies the
            # operator site without forcing literal city equality.
            if len(candidates) != 1:
                reason = "no_unique_operator_site"
        if reason is not None:
            unresolved.append({"opis_id": opis_id, "reason": reason})
            continue
        proposed[opis_id] = candidates[0]
    for opis_id, page in proposed.items():
        linked.append(
            {
                "opis_id": opis_id,
                **page,
                "site_status": "verified",
                "routing_status": "pending_check",
            }
        )
    return linked, unresolved


def validate_catalog(catalog: dict, source: dict[str, dict], source_hash: str) -> None:
    if (
        catalog["status"] != "complete_attempt"
        or catalog["source_sha256"] != source_hash
        or catalog["source_id_count"] != len(source)
        or catalog["verified_count"] != len(catalog["records"])
        or catalog["unresolved_count"] != len(catalog["unresolved"])
        or catalog["verified_count"] + catalog["unresolved_count"] != len(source)
        or catalog["parsed_page_count"] != len(catalog["pages"])
        or len(catalog["sitemap_sha256"]) != 64
    ):
        raise ValueError("Pilot site catalog is incomplete or stale")
    seen_ids = set()
    for record in catalog["records"]:
        opis_id = record["opis_id"]
        row = source.get(opis_id)
        expected_country = "US" if record["state"] in STATES else "CA"
        if (
            row is None
            or opis_id in seen_ids
            or row["brands"] != [record["brand"]]
            or row["branches"] != [record["store_number"]]
            or row["regions"] != [record["state"]]
            or record["country"] != expected_country
            or record["site_status"] != "verified"
            or record["routing_status"] != "pending_check"
            or not record["address"]
            or not record["evidence_url"].startswith(
                "https://locations.pilotflyingj.com/"
            )
            or len(record["page_sha256"]) != 64
            or not -90 <= record["latitude"] <= 90
            or not -180 <= record["longitude"] <= 180
        ):
            raise ValueError(f"Pilot catalog identity differs for {opis_id}")
        seen_ids.add(opis_id)
    for item in catalog["unresolved"]:
        opis_id = item["opis_id"]
        if opis_id not in source or opis_id in seen_ids:
            raise ValueError(f"Pilot catalog unresolved ID differs for {opis_id}")
        seen_ids.add(opis_id)
    if seen_ids != set(source):
        raise ValueError("Pilot catalog omits source IDs")
    linked, unresolved = reconcile(source, catalog["pages"])
    if linked != catalog["records"] or unresolved != catalog["unresolved"]:
        raise ValueError("Pilot catalog decisions differ from page evidence")
