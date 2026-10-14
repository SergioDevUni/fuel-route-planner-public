"""Shared green river rules used by review and preparation tools."""

import csv
import re
from pathlib import Path

from app.stations.catalogs.loves_crosswalk import read_codes


def verify_source_rows(evidence: dict, source: Path) -> None:
    expected = evidence["source_rows"]
    found = []
    ids = {item["opis_id"] for item in expected}
    with source.open(encoding="utf-8-sig", newline="") as stream:
        for row_number, row in enumerate(csv.DictReader(stream), start=2):
            if row["OPIS Truckstop ID"].strip() not in ids:
                continue
            match = re.search(r"#\s*(\d+)\b", row["Truckstop Name"])
            if not match or "LOVES" not in row["Truckstop Name"].upper():
                raise ValueError(f"unexpected Green River source name at {row_number}")
            if (
                row["City"].strip() != "Green River"
                or row["State"].strip() != "UT"
                or row["Address"].strip() != "I-70, EXIT 160"
            ):
                raise ValueError(
                    f"unexpected Green River source location at {row_number}"
                )
            found.append(
                {
                    "row_number": row_number,
                    "opis_id": row["OPIS Truckstop ID"].strip(),
                    "store_number": match.group(1),
                    "price": row["Retail Price"].strip(),
                }
            )
    if found != expected:
        raise ValueError("Green River source rows differ from reviewed evidence")


def verify_links(evidence: dict, workbook: Path) -> None:
    sites = {site["site_id"]: site for site in evidence["sites"]}
    links = evidence["source_links"]
    if {link["opis_id"] for link in links} != {"1914", "70793"}:
        raise ValueError("Green River OPIS set differs")
    source_stores: dict[str, set[str]] = {}
    for row in evidence["source_rows"]:
        source_stores.setdefault(row["opis_id"], set()).add(row["store_number"])
    if len({link["site_id"] for link in links}) != len(links):
        raise ValueError("Green River source IDs cannot collapse onto one site")
    for link in links:
        site = sites.get(link["site_id"])
        if (
            site is None
            or source_stores.get(link["opis_id"]) != {site["store_number"]}
            or link["status"] != "csv_store_match"
        ):
            raise ValueError("Green River link differs from CSV store identity")
    codes = [
        code for code in read_codes(workbook) if code["store_number"] in {119, 792}
    ]
    if len(codes) != 1 or codes[0] != {
        "store_number": 792,
        "city": "Green River",
        "state": "UT",
        "opis_id": 70793,
    }:
        raise ValueError("Love's workbook no longer has reviewed Green River codes")
