"""Link exact numbered CSV Kwik stores to the operator's public store list."""

import argparse
import hashlib
import html
import json
import os
import re

import httpx

from app.stations.catalogs.kwiktrip_site_catalog import NAME as NAME
from app.stations.catalogs.kwiktrip_site_catalog import STORE_LIST_URL as STORE_LIST_URL
from app.stations.catalogs.kwiktrip_site_catalog import load_stores as load_stores
from app.stations.catalogs.kwiktrip_site_catalog import (
    source_stations as source_stations,
)
from app.stations.catalogs.kwiktrip_site_catalog import (
    validate_catalog as validate_catalog,
)
from app.stations.catalogs.paths import ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE
from app.stations.catalogs.source_queue import build_queue

WORKBOOK = ROOT / "var/operator_review/kwiktrip_stores.csv"
OUTPUT = ROOT / "var/operator_review/kwiktrip_site_catalog.json"


def fetch_workbook() -> None:
    page = httpx.get(STORE_LIST_URL, timeout=30, follow_redirects=True)
    page.raise_for_status()
    match = re.search(
        r'href="([^"]*kwiktrip_table_export=1[^"]*format=csv[^"]*)"',
        page.text,
    )
    if match is None:
        raise ValueError("operator CSV download link missing")
    url = html.unescape(match.group(1))
    response = httpx.get(url, timeout=30, follow_redirects=True)
    response.raise_for_status()
    load_stores(response.content)
    temporary = WORKBOOK.with_suffix(".csv.tmp")
    temporary.write_bytes(response.content)
    os.replace(temporary, WORKBOOK)


def build() -> dict:
    workbook_bytes = WORKBOOK.read_bytes()
    stores = load_stores(workbook_bytes)
    source = source_stations()
    queue = build_queue()
    eligible = {row["opis_id"] for row in queue["stations"]}
    prior = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else None
    if prior is not None:
        validate_catalog(prior, workbook_bytes)
        eligible.update(item["opis_id"] for item in prior["records"])
    records = []
    unresolved = []
    for opis_id in sorted(eligible, key=int):
        item = source.get(opis_id)
        if item is None:
            continue
        store = stores.get(item["store_number"])
        reason = None
        if store is None:
            reason = "operator_store_missing"
        elif item["states"] != [store["State"].strip().upper()]:
            reason = "state_conflict"
        elif store["Store Name"].upper().split(" #")[0] != item["brand"] and store[
            "City"
        ].casefold().strip() not in {city.casefold() for city in item["cities"]}:
            reason = "brand_and_city_conflict"
        elif store["Sells Diesel"] != "Yes":
            reason = "diesel_not_listed"
        if reason is not None:
            unresolved.append(
                {
                    "opis_id": opis_id,
                    "store_number": item["store_number"],
                    "reason": reason,
                    "csv_cities": item["cities"],
                    "operator_city": store["City"].strip() if store else None,
                }
            )
            continue
        assert store is not None
        city_matches = store["City"].casefold().strip() in {
            city.casefold() for city in item["cities"]
        }
        brand_matches = store["Store Name"].upper().split(" #")[0] == item["brand"]
        records.append(
            {
                "opis_id": opis_id,
                "brand": item["brand"],
                "store_number": item["store_number"],
                "site_id": f"kwiktrip:{item['store_number']}",
                "address": store["Address"].strip(),
                "city": store["City"].strip(),
                "state": store["State"].strip().upper(),
                "latitude": float(store["Latitude"]),
                "longitude": float(store["Longitude"]),
                "identity_conflict": (
                    "city_label_differs"
                    if not city_matches
                    else "brand_label_differs"
                    if not brand_matches
                    else None
                ),
            }
        )
    if prior is not None and not {item["opis_id"] for item in prior["records"]} <= {
        item["opis_id"] for item in records
    }:
        raise ValueError("existing Kwik Trip links disappeared")
    catalog = {
        "status": "complete_attempt",
        "source_sha256": queue["source_sha256"],
        "source_url": STORE_LIST_URL,
        "workbook_sha256": hashlib.sha256(workbook_bytes).hexdigest(),
        "verified_count": len(records),
        "unresolved_count": len(unresolved),
        "shared_site_groups": {
            number: sorted(
                [
                    record["opis_id"]
                    for record in records
                    if record["store_number"] == number
                ],
                key=int,
            )
            for number in {record["store_number"] for record in records}
            if sum(record["store_number"] == number for record in records) > 1
        },
        "records": records,
        "unresolved": unresolved,
    }
    validate_catalog(catalog, workbook_bytes)
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, OUTPUT)
    return catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    if args.fetch:
        fetch_workbook()
    result = build()
    print(
        result["verified_count"], "verified;", result["unresolved_count"], "unresolved"
    )


if __name__ == "__main__":
    main()
