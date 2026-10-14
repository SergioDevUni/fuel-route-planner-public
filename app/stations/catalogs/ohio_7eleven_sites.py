"""Shared ohio 7eleven sites rules used by review and preparation tools."""

import csv
import hashlib
import json
import re
from pathlib import Path

from app.stations.catalogs.comdata_matching import (
    house_number,
    plain,
    point_coordinates,
)
from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE

EVIDENCE = "data/review/ohio_7eleven_sites.json"

TANK_PDF = "var/operator_review/ohio_7eleven_tank_list.pdf"

TURNPIKE_PDF = "var/operator_review/ohio_turnpike_plazas_2020.pdf"

OH_PLACES = "var/overture_states/oh_places.ndjson"

OH_MANIFEST = "var/overture_states/oh_manifest.json"


def validate(root: Path = ROOT) -> dict:
    evidence = json.loads((root / EVIDENCE).read_text())
    for path, key in (
        (TANK_PDF, "tank_register_sha256"),
        (TURNPIKE_PDF, "turnpike_sha256"),
    ):
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != evidence[key]:
            raise ValueError(f"Ohio site source changed: {path}")
    places_bytes = (root / OH_PLACES).read_bytes()
    manifest = json.loads((root / OH_MANIFEST).read_text())
    if hashlib.sha256(places_bytes).hexdigest() != manifest["output_sha256"]:
        raise ValueError("Ohio mapped-site source changed")
    wanted = {r["overture_id"] for r in evidence["records"] if r["overture_id"]}
    places = {
        p["id"]: p
        for p in map(json.loads, places_bytes.splitlines())
        if p["id"] in wanted
    }
    source: dict[str, list[dict[str, str]]] = {}
    with (root / SOURCE.relative_to(ROOT)).open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        for row in csv.DictReader(stream):
            source.setdefault(row["OPIS Truckstop ID"].strip(), []).append(row)
    seen_ids: set[str] = set()
    for record in evidence["records"]:
        opis_id = record["opis_id"]
        rows = source.get(opis_id, [])
        if (
            opis_id in seen_ids
            or len(rows) != 1
            or not re.fullmatch(
                rf"7\s*-\s*ELEVEN\s*#{record['store_number']}",
                rows[0]["Truckstop Name"].strip(),
                re.I,
            )
            or plain(rows[0]["City"]) != plain(record["city"])
            or rows[0]["State"].strip().upper() != "OH"
            or record["state"] != "OH"
            or not (40 <= record["latitude"] <= 42)
            or not (-85 <= record["longitude"] <= -80)
        ):
            raise ValueError(f"Ohio store identity differs: {opis_id}")
        place_id = record["overture_id"]
        if place_id:
            place = places.get(place_id)
            if (
                place is None
                or record["site_id"] != f"overture:{place_id}"
                or point_coordinates(place["point_wkt"])
                != (record["latitude"], record["longitude"])
                or place["basic_category"]
                not in {"gas_station", "convenience_store", "fueling_station"}
                or (place["confidence"] or 0) < 0.75
                or not any(
                    address.get("region") == "OH"
                    and address.get("country") == "US"
                    and plain(address.get("locality") or "") == plain(record["city"])
                    and house_number(address.get("freeform") or "")
                    == house_number(record["address"])
                    for address in place["addresses"] or []
                )
            ):
                raise ValueError(f"Ohio map point differs: {opis_id}")
        elif (
            opis_id != "61449"
            or record["site_id"] != "ohio-turnpike:portage:westbound"
            or (record["latitude"], record["longitude"]) != (41.244826, -81.178404)
        ):
            raise ValueError(f"Ohio turnpike point differs: {opis_id}")
        seen_ids.add(opis_id)
    if seen_ids != {
        "7396",
        "61421",
        "61430",
        "61439",
        "61449",
        "64223",
        "64224",
        "67678",
    }:
        raise ValueError("Ohio 7-Eleven reviewed batch is incomplete")
    return evidence
