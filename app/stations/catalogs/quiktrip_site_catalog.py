"""Shared quiktrip site catalog rules used by review and preparation tools."""

import hashlib
import re
from collections import defaultdict

from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE

PAGES = ROOT / "var/operator_review/quiktrip_pages.json"

CSV_NUMBER = re.compile(r"\bQUIK\s*TRIP\s*#\s*(\d+)\b", re.I)


def source_stations() -> dict[str, dict]:
    import csv

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            grouped[row["OPIS Truckstop ID"].strip()].append(row)
    result = {}
    for opis_id, rows in grouped.items():
        names = {row["Truckstop Name"].strip() for row in rows}
        matches = [CSV_NUMBER.search(name) for name in names]
        if not matches or not all(matches):
            continue
        numbers = {match.group(1).lstrip("0") for match in matches if match}
        if len(numbers) != 1:
            continue
        result[opis_id] = {
            "store_number": next(iter(numbers)),
            "states": sorted({row["State"].strip().upper() for row in rows}),
            "cities": sorted({row["City"].strip() for row in rows}),
        }
    return result


def validate_catalog(catalog: dict, pages: dict) -> None:
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if (
        catalog["source_sha256"] != source_hash
        or pages["source_sha256"] != source_hash
        or catalog["pages_sha256"] != hashlib.sha256(PAGES.read_bytes()).hexdigest()
        or catalog["sitemap_sha256"] != pages["sitemap_sha256"]
        or pages["page_count"] + pages["failure_count"] != pages["selected_url_count"]
        or catalog["verified_count"] != len(catalog["records"])
        or catalog["unresolved_count"] != len(catalog["unresolved"])
    ):
        raise ValueError("QuikTrip catalog source or counts differ")
    source = source_stations()
    by_number: dict[str, list[dict]] = defaultdict(list)
    for page in pages["pages"]:
        by_number[page["store_number"]].append(page)
    ids: set[str] = set()
    for record in catalog["records"]:
        opis_id = record["opis_id"]
        source_row = source.get(opis_id)
        matches = by_number.get(record["store_number"], [])
        if (
            opis_id in ids
            or source_row is None
            or source_row["store_number"] != record["store_number"]
            or len(matches) != 1
            or source_row["states"] != [matches[0]["state"]]
            or record.get("identity_conflict")
            != (
                None
                if matches[0]["city"].casefold()
                in {city.casefold() for city in source_row["cities"]}
                else "city_label_differs"
            )
            or any(record[key] != matches[0][key] for key in matches[0])
            or record["site_id"] != f"quiktrip:{record['store_number']}"
        ):
            raise ValueError(f"QuikTrip identity differs for {opis_id}")
        ids.add(opis_id)
    unresolved_ids = {item["opis_id"] for item in catalog["unresolved"]}
    if len(unresolved_ids) != len(catalog["unresolved"]) or ids & unresolved_ids:
        raise ValueError("QuikTrip unresolved rows conflict")
