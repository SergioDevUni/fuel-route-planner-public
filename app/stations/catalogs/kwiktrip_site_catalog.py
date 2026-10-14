"""Shared kwiktrip site catalog rules used by review and preparation tools."""

import csv
import hashlib
import re
from collections import defaultdict

from app.stations.catalogs.paths import SOURCE as SOURCE

STORE_LIST_URL = "https://www.kwiktrip.com/Maps-Downloads/Store-List"

NAME = re.compile(r"\b(KWIK\s+(?:TRIP|STAR))\s*#\s*(\d+)\b", re.I)


def load_stores(data: bytes) -> dict[str, dict[str, str]]:
    rows = list(csv.DictReader(data.decode("utf-8-sig").splitlines()))
    if len(rows) < 500:
        raise ValueError("operator store list unexpectedly small")
    stores = {}
    for row in rows:
        number = row["Store Number"].strip().lstrip("0")
        if number in stores or not number.isdigit():
            raise ValueError(f"duplicate or invalid operator store number {number}")
        stores[number] = row
    return stores


def source_stations() -> dict[str, dict]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            grouped[row["OPIS Truckstop ID"].strip()].append(row)
    result = {}
    for opis_id, rows in grouped.items():
        names = sorted({row["Truckstop Name"].strip() for row in rows})
        parsed = [NAME.search(name) for name in names]
        if not all(parsed):
            continue
        identities = {
            (" ".join(match.group(1).upper().split()), match.group(2).lstrip("0"))
            for match in parsed
            if match is not None
        }
        if len(identities) != 1:
            continue
        brand, number = next(iter(identities))
        result[opis_id] = {
            "brand": brand,
            "store_number": number,
            "names": names,
            "cities": sorted({row["City"].strip() for row in rows}),
            "states": sorted({row["State"].strip().upper() for row in rows}),
        }
    return result


def validate_catalog(catalog: dict, workbook_bytes: bytes) -> None:
    if (
        catalog["source_sha256"] != hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        or catalog["workbook_sha256"] != hashlib.sha256(workbook_bytes).hexdigest()
        or catalog["source_url"] != STORE_LIST_URL
        or catalog["verified_count"] != len(catalog["records"])
        or catalog["unresolved_count"] != len(catalog["unresolved"])
    ):
        raise ValueError("Kwik Trip catalog source or counts differ")
    stores = load_stores(workbook_bytes)
    source = source_stations()
    used_ids: set[str] = set()
    site_groups: dict[str, list[str]] = defaultdict(list)
    for record in catalog["records"]:
        opis_id = record["opis_id"]
        source_row = source.get(opis_id)
        store = stores.get(record["store_number"])
        operator_brand = store["Store Name"].upper().split(" #")[0] if store else ""
        city_matches = (
            bool(
                store
                and store["City"].casefold().strip()
                in {city.casefold() for city in source_row["cities"]}
            )
            if source_row
            else False
        )
        brand_matches = bool(source_row and operator_brand == source_row["brand"])
        expected_conflict = (
            "city_label_differs"
            if brand_matches and not city_matches
            else "brand_label_differs"
            if city_matches and not brand_matches
            else None
        )
        if (
            opis_id in used_ids
            or source_row is None
            or store is None
            or source_row["brand"] != record["brand"]
            or source_row["store_number"] != record["store_number"]
            or source_row["states"] != [store["State"].strip().upper()]
            or not (brand_matches or city_matches)
            or record.get("identity_conflict") != expected_conflict
            or record["address"] != store["Address"].strip()
            or record["city"] != store["City"].strip()
            or record["state"] != store["State"].strip().upper()
            or record["latitude"] != float(store["Latitude"])
            or record["longitude"] != float(store["Longitude"])
            or store["Sells Diesel"] != "Yes"
            or record["site_id"] != f"kwiktrip:{record['store_number']}"
        ):
            raise ValueError(f"Kwik Trip identity differs for {opis_id}")
        used_ids.add(opis_id)
        site_groups[record["store_number"]].append(opis_id)
    unresolved_ids = [item["opis_id"] for item in catalog["unresolved"]]
    if len(set(unresolved_ids)) != len(unresolved_ids) or used_ids & set(
        unresolved_ids
    ):
        raise ValueError("Kwik Trip unresolved identities overlap accepted links")
    if catalog["shared_site_groups"] != {
        number: sorted(ids, key=int)
        for number, ids in site_groups.items()
        if len(ids) > 1
    }:
        raise ValueError("Kwik Trip shared physical sites differ")
