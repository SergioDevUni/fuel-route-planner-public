"""Shared illinois gasnwash sites rules used by review and preparation tools."""

import csv
import hashlib
import json
import re
from collections import defaultdict

from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE

REVIEW = ROOT / "data/review/illinois_gasnwash_sites.json"

CATALOG = ROOT / "var/overture_states/il_places.ndjson"

MANIFEST = ROOT / "var/overture_states/il_manifest.json"


def compact(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def validate() -> dict:
    review = json.loads(REVIEW.read_bytes())
    catalog_bytes = CATALOG.read_bytes()
    manifest = json.loads(MANIFEST.read_bytes())
    if (
        review["source_sha256"] != hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        or review["overture_catalog_sha256"]
        != hashlib.sha256(catalog_bytes).hexdigest()
        or review["overture_catalog_sha256"] != manifest["output_sha256"]
        or manifest["state"] != "IL"
    ):
        raise ValueError("Gas N Wash source changed")
    source: dict[str, list[dict[str, str]]] = defaultdict(list)
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            source[row["OPIS Truckstop ID"].strip()].append(row)
    places = {
        place["id"]: place
        for place in (json.loads(line) for line in catalog_bytes.splitlines())
    }
    used_ids: set[str] = set()
    for record in review["records"]:
        opis_id = record["opis_id"]
        place_id = record["overture_id"]
        rows = source.get(opis_id, [])
        html = ROOT / "var/operator_review" / record["source_file"]
        html_bytes = html.read_bytes()
        place = places.get(place_id)
        if (
            not rows
            or opis_id in used_ids
            or {row["State"] for row in rows} != {"IL"}
            or {compact(row["Truckstop Name"]) for row in rows}
            != {compact(record["csv_name"])}
            or {compact(row["City"]) for row in rows} != {compact(record["csv_city"])}
            or {compact(row["Address"]) for row in rows}
            != {compact(record["csv_address"])}
            or hashlib.sha256(html_bytes).hexdigest() != record["source_sha256"]
            or record["source_address"] not in html_bytes.decode(errors="replace")
            or "Gas N Wash" not in html_bytes.decode(errors="replace")
            or place is None
            or compact(place["name"] or "") != "gas n wash"
            or record["site_id"] != f"overture:{place_id}"
        ):
            raise ValueError(f"Gas N Wash identity changed for {opis_id}")
        address = next(
            (
                item
                for item in place["addresses"] or []
                if item.get("region") == "IL"
                and item.get("country") == "US"
                and compact(item.get("locality") or "") == compact(record["csv_city"])
            ),
            None,
        )
        point = re.fullmatch(r"POINT \((-?[\d.]+) (-?[\d.]+)\)", place["point_wkt"])
        if (
            address is None
            or re.match(r"\d+", address["freeform"] or "") is None
            or re.match(r"\d+", record["source_address"]) is None
            or re.match(r"\d+", address["freeform"] or "").group()
            != re.match(r"\d+", record["source_address"]).group()
            or not set(compact(address["freeform"] or "").split())
            & set(compact(record["source_address"]).split())
            or point is None
            or (float(point.group(2)), float(point.group(1)))
            != (record["latitude"], record["longitude"])
        ):
            raise ValueError(f"Gas N Wash mapped point changed for {opis_id}")
        used_ids.add(opis_id)
    return review
