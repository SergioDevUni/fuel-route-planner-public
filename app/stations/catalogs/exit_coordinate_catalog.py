"""Shared exit coordinate catalog rules used by review and preparation tools."""

import csv
import io
import json
import math
import re
import tarfile
from collections import defaultdict
from pathlib import Path

from shapely.geometry import Point, shape

from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.files import sha256 as sha256

RELEASE = "var/openinterstate/release-2026-07-13-gha-33.tar.gz"

CSV_COORDINATES = "var/station_locations/csv_coordinate_catalog.json"

STATES = "data/prepared/locations/states_detailed.geojson"

OUTPUT = "var/station_locations/exit_coordinate_catalog.json"

MAX_CITY_DISTANCE_MILES = 20.0


def distance_miles(a: float, b: float, c: float, d: float) -> float:
    lat = math.radians(c - a)
    lon = math.radians(d - b)
    value = (
        math.sin(lat / 2) ** 2
        + math.cos(math.radians(a)) * math.cos(math.radians(c)) * math.sin(lon / 2) ** 2
    )
    return 3958.7613 * 2 * math.asin(math.sqrt(value))


def road_and_exits(address: str) -> tuple[set[str], list[str]]:
    text = address.upper()
    roads = set()
    for match in re.finditer(r"\bI[- ]?(\d{1,3})(?:\s*/\s*(\d{1,3}))?\b", text):
        roads.add(f"I-{match.group(1)}")
        if match.group(2):
            roads.add(f"I-{match.group(2)}")
    number = re.search(
        r"\bEXIT\s*(\d+[A-Z]?)(?:\s*[-/]\s*([A-Z](?![A-Z0-9])|\d+[A-Z]?))?",
        text,
    )
    if not number:
        return roads, []
    values = [number.group(1)]
    suffix = number.group(2)
    if suffix:
        values = [number.group(1) + suffix] if len(suffix) == 1 else values + [suffix]
    return roads, values


def _exit_index(root: Path) -> dict[tuple[str, str], list[dict]]:
    with tarfile.open(root / RELEASE, "r:gz") as archive:
        member = next(
            item
            for item in archive.getmembers()
            if item.name.endswith("/csv/corridor_exits.csv")
        )
        stream = archive.extractfile(member)
        if stream is None:
            raise ValueError("OpenInterstate exit table missing")
        exits = list(csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8")))
    index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for exit_record in exits:
        if exit_record["exit_number"] and exit_record["lat"] and exit_record["lon"]:
            key = (
                exit_record["interstate_name"].upper(),
                exit_record["exit_number"].upper().replace(" ", ""),
            )
            index[key].append(exit_record)
    return index


def build_catalog(root: Path = ROOT) -> dict:
    fallback_path = root / CSV_COORDINATES
    fallback = json.loads(fallback_path.read_text())
    boundaries = {
        item["code"]: shape(item["geometry"])
        for item in json.loads((root / STATES).read_text())["states"]
    }
    exits = _exit_index(root)
    records = []
    for station in fallback["records"]:
        if station["country"] != "US":
            continue
        roads, exit_numbers = road_and_exits(station["address"])
        if not roads or not exit_numbers:
            continue
        options = [
            candidate
            for road in roads
            for number in exit_numbers
            for candidate in exits.get((road, number), [])
            if boundaries[station["state"]].covers(
                Point(float(candidate["lon"]), float(candidate["lat"]))
            )
        ]
        if not options:
            continue
        ranked = sorted(
            options,
            key=lambda item: (
                distance_miles(
                    station["latitude"],
                    station["longitude"],
                    float(item["lat"]),
                    float(item["lon"]),
                ),
                item["exit_id"],
                item["interstate_name"],
                item["exit_number"],
            ),
        )
        match = ranked[0]
        distance = distance_miles(
            station["latitude"],
            station["longitude"],
            float(match["lat"]),
            float(match["lon"]),
        )
        if distance > MAX_CITY_DISTANCE_MILES:
            continue
        records.append(
            {
                "opis_id": station["opis_id"],
                "country": station["country"],
                "name": station["name"],
                "address": station["address"],
                "city": station["city"],
                "state": station["state"],
                "site_id": f"openinterstate:{match['exit_id']}",
                "exit_id": match["exit_id"],
                "interstate_name": match["interstate_name"],
                "exit_number": match["exit_number"],
                "exit_name": match["exit_name"],
                "latitude": float(match["lat"]),
                "longitude": float(match["lon"]),
                "city_distance_miles": round(distance, 3),
                "location_precision": "interstate_exit",
                "location_source": "openinterstate_osm",
            }
        )
    return {
        "schema_version": 1,
        "source_sha256": fallback["source_sha256"],
        "csv_coordinate_catalog_sha256": sha256(fallback_path),
        "openinterstate_release_sha256": sha256(root / RELEASE),
        "states_sha256": sha256(root / STATES),
        "max_city_distance_miles": MAX_CITY_DISTANCE_MILES,
        "records": sorted(records, key=lambda item: int(item["opis_id"])),
    }


def validate_catalog(root: Path, source_hash: str) -> dict:
    catalog = json.loads((root / OUTPUT).read_text())
    if catalog["source_sha256"] != source_hash or catalog != build_catalog(root):
        raise ValueError("interstate exit catalog differs from pinned sources")
    return catalog
