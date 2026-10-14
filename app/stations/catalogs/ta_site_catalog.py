"""Shared ta site catalog rules used by review and preparation tools."""

import csv
import io
import math
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

from app.domain.text import canonical_text
from app.locations.states import state_code

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

NUMBER = re.compile(r"#\s*(\d+)\b")

WORDS = re.compile(r"[a-z0-9]+")


def operator_brand(name: str) -> str | None:
    upper = name.strip().upper()
    if upper.startswith("TA EXPRESS "):
        return "ta_express"
    if upper.startswith("TA "):
        return "ta"
    if re.match(r"^PETRO(?:\s+STOPPING\s+CENTER|\s+STOP)?\s", upper):
        return "petro"
    return None


def title_key(value: str) -> str:
    text = canonical_text(value)
    text = re.sub(
        r"^(?:ta express|ta|petro stopping center|petro stop|petro)\b", "", text
    )
    text = re.sub(r"\b(?:travel center|travelcenter|travel plaza)\b", "", text)
    return " ".join(WORDS.findall(text))


def source_stations(source: Path) -> dict[str, dict]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    with source.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if operator_brand(row["Truckstop Name"]):
                groups[row["OPIS Truckstop ID"].strip()].append(row)
    result = {}
    for opis_id, rows in groups.items():
        result[opis_id] = {
            "opis_id": opis_id,
            "brands": sorted({operator_brand(row["Truckstop Name"]) for row in rows}),
            "site_numbers": sorted(
                {
                    int(match.group(1))
                    for row in rows
                    for match in NUMBER.finditer(row["Truckstop Name"])
                }
            ),
            "cities": sorted({canonical_text(row["City"]) for row in rows}),
            "states": sorted({row["State"].strip().upper() for row in rows}),
            "title_keys": sorted({title_key(row["Truckstop Name"]) for row in rows}),
            "source_names": sorted({row["Truckstop Name"].strip() for row in rows}),
        }
    return result


def read_workbook(content: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [
                "".join(part.text or "" for part in item.iter(f"{NS}t"))
                for item in strings.findall(f"{NS}si")
            ]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.findall(f"{NS}sheetData/{NS}row"):
        cells = {}
        for cell in row.findall(f"{NS}c"):
            column = re.match(r"[A-Z]+", cell.attrib["r"])
            if column is None:
                raise ValueError("TA workbook cell lacks column")
            value = cell.find(f"{NS}v")
            if value is None or value.text is None:
                continue
            cells[column.group()] = (
                shared[int(value.text)] if cell.attrib.get("t") == "s" else value.text
            )
        rows.append(cells)
    if not rows or [rows[0].get(col) for col in "ABDEHMN"] != [
        "Site ID",
        "Brand",
        "State",
        "City",
        "Address",
        "Latitude",
        "Longitude",
    ]:
        raise ValueError("TA workbook columns changed")
    sites = []
    for row in rows[1:]:
        if row.get("B") not in {"TA", "TA Express", "Petro"}:
            continue
        latitude = float(row["M"])
        longitude = float(row["N"])
        if (
            not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not 24 <= latitude <= 50
            or not -125 <= longitude <= -66
            or not row.get("H")
        ):
            raise ValueError("TA workbook has invalid site point or address")
        site_number = int(row["A"])
        sites.append(
            {
                "site_id": f"ta:{site_number}",
                "site_number": site_number,
                "brand": row["B"].lower().replace(" ", "_"),
                "name": row["F"],
                "city": row["E"],
                "state": state_code(row["D"]),
                "address": row["H"],
                "latitude": latitude,
                "longitude": longitude,
            }
        )
    if len({site["site_id"] for site in sites}) != len(sites):
        raise ValueError("TA workbook repeats a site ID")
    return sites


def reconcile(
    source: dict[str, dict], sites: list[dict]
) -> tuple[list[dict], list[dict]]:
    proposed = {}
    unresolved = []
    for opis_id, row in sorted(source.items(), key=lambda item: int(item[0])):
        if len(row["brands"]) != 1 or len(row["states"]) != 1:
            unresolved.append(
                {"opis_id": opis_id, "reason": "source_identity_ambiguous"}
            )
            continue
        candidates = [
            site
            for site in sites
            if site["brand"] == row["brands"][0] and site["state"] == row["states"][0]
        ]
        if row["brands"][0] == "petro" and len(row["site_numbers"]) == 1:
            candidates = [
                site
                for site in candidates
                if site["site_number"] == row["site_numbers"][0]
            ]
            candidates = [
                site
                for site in candidates
                if canonical_text(site["city"]) in row["cities"]
                or any(city in canonical_text(site["name"]) for city in row["cities"])
            ]
        else:
            exact = [
                site
                for site in candidates
                if title_key(site["name"]) in row["title_keys"]
            ]
            same_city = [
                site for site in exact if canonical_text(site["city"]) in row["cities"]
            ]
            # The CSV may name the exit's municipality while TA uses the
            # nearby mailing city. A unique operator title within the state
            # still identifies the site (e.g. Jacksonville South / St Johns).
            candidates = same_city or exact
        if len(candidates) != 1:
            unresolved.append({"opis_id": opis_id, "reason": "no_unique_operator_site"})
            continue
        proposed[opis_id] = candidates[0]
    linked = [
        {
            "opis_id": opis_id,
            **site,
            "site_status": "verified",
            "routing_status": "pending_check",
        }
        for opis_id, site in proposed.items()
    ]
    return linked, unresolved


def validate_catalog(catalog: dict, source: dict[str, dict], source_hash: str) -> None:
    if (
        catalog["status"] != "complete_attempt"
        or catalog["source_sha256"] != source_hash
        or catalog["source_id_count"] != len(source)
        or catalog["verified_count"] != len(catalog["records"])
        or catalog["unresolved_count"] != len(catalog["unresolved"])
        or catalog["verified_count"] + catalog["unresolved_count"] != len(source)
    ):
        raise ValueError("TA catalog is incomplete or stale")
    linked, unresolved = reconcile(source, catalog["sites"])
    if linked != catalog["records"] or unresolved != catalog["unresolved"]:
        raise ValueError("TA catalog decisions differ from workbook sites")
