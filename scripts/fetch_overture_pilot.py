"""Fetch a bounded Overture Places review extract; no station is accepted."""

import argparse
import hashlib
import json
import math
import re
import time
from pathlib import Path

import duckdb

from app.stations.catalogs.paths import OVERTURE_RELEASE as RELEASE

ROOT = Path(__file__).resolve().parents[1]

SOURCE = f"s3://overturemaps-us-west-2/release/{RELEASE}/theme=places/type=place/*"
SEARCH_RADIUS_KM = 75
REGIONS = {
    "chicago": {
        "bbox": (-88.3, 41.4, -87.3, 42.2),
        "names": "Pilot|Road Ranger|Speedway|Loves|Thorntons|Good To Go",
    },
    "imperial_ca": {
        "bbox": (-116.8, 32.55, -115.3, 33.9),
        "names": "Pilot|Chevron|Speedway|Loves|One9|Soco|Shelee|Travel Center",
    },
}


def boxes_for_center(latitude: float, longitude: float) -> list[tuple[float, ...]]:
    lat_delta = SEARCH_RADIUS_KM / 111.0
    lon_delta = SEARCH_RADIUS_KM / (111.0 * max(0.01, math.cos(math.radians(latitude))))
    south = max(-90.0, latitude - lat_delta)
    north = min(90.0, latitude + lat_delta)
    west = longitude - lon_delta
    east = longitude + lon_delta
    if west < -180.0:
        return [(-180.0, south, east, north), (west + 360.0, south, 180.0, north)]
    if east > 180.0:
        return [(west, south, 180.0, north), (-180.0, south, east - 360.0, north)]
    return [(west, south, east, north)]


def station_boxes(station_id: str) -> tuple[list[tuple[float, ...]], list[str]]:
    seeds = json.loads(
        (ROOT / "var/pilot/geographic_seeds.json").read_text(encoding="utf-8")
    )
    station = next(
        (item for item in seeds["stations"] if item["opis_id"] == station_id),
        None,
    )
    if station is None or not station["centers"]:
        raise ValueError("station has no prepared geographic search centers")
    boxes: list[tuple[float, ...]] = []
    for center in station["centers"]:
        boxes.extend(boxes_for_center(center["latitude"], center["longitude"]))
    return boxes, [center["place_id"] for center in station["centers"]]


def box_filter(boxes: list[tuple[float, ...]]) -> str:
    return " OR ".join(
        "(bbox.xmin BETWEEN {0} AND {2} AND bbox.ymin BETWEEN {1} AND {3})".format(*box)
        for box in boxes
    )


def station_name_filter(station_id: str) -> str:
    sample = json.loads((ROOT / "var/pilot/stations.json").read_text(encoding="utf-8"))
    station = next(
        (item for item in sample["stations"] if item["opis_id"] == station_id),
        None,
    )
    if station is None:
        raise ValueError("station ID missing from the 100-ID sample")
    chains = [
        "Pilot",
        "Road Ranger",
        "Speedway",
        "Loves",
        "Thorntons",
        "One9",
        "Chevron",
        "Travel Center",
    ]
    aliases = [re.escape(alias) for alias in station["aliases"] if alias.strip()]
    return "|".join(dict.fromkeys([*chains, *aliases]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--region", choices=REGIONS, default="chicago")
    scope.add_argument("--station-id")
    args = parser.parse_args()
    if args.station_id is not None:
        if not args.station_id.isdecimal():
            raise ValueError("station ID must be numeric")
        boxes, place_ids = station_boxes(args.station_id)
        names = station_name_filter(args.station_id)
        output = ROOT / f"var/pilot/stations/{args.station_id}_overture_places.ndjson"
        manifest_path = ROOT / f"var/pilot/stations/{args.station_id}_manifest.json"
    else:
        region = REGIONS[args.region]
        boxes = [region["bbox"]]
        place_ids = []
        names = region["names"]
        output = ROOT / f"var/pilot/{args.region}_overture_places.ndjson"
        manifest_path = ROOT / f"var/pilot/{args.region}_overture_manifest.json"
    connection = duckdb.connect()
    connection.execute("LOAD httpfs")
    query = f"""
        SELECT id, names.primary AS name, addresses, basic_category,
               confidence, operating_status, sources,
               ST_AsText(geometry) AS point
        FROM read_parquet('{SOURCE}', hive_partitioning=1)
        WHERE ({box_filter(boxes)})
          AND (basic_category IN ('gas_station', 'convenience_store', 'truck_stop')
               OR regexp_matches(names.primary, ?))
        ORDER BY id
    """
    started = time.monotonic()
    rows = connection.execute(query, [f"(?i){names}"]).fetchall()
    elapsed = round(time.monotonic() - started, 3)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for (
            place_id,
            name,
            addresses,
            category,
            confidence,
            operating_status,
            sources,
            point,
        ) in rows:
            stream.write(
                json.dumps(
                    {
                        "id": place_id,
                        "name": name,
                        "addresses": addresses,
                        "basic_category": category,
                        "confidence": confidence,
                        "operating_status": operating_status,
                        "sources": sources,
                        "point_wkt": point,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
    data = output.read_bytes()
    manifest = {
        "release": RELEASE,
        "source": SOURCE,
        "bbox_west_south_east_north": boxes,
        "gazetteer_search_place_ids": place_ids,
        "search_radius_km": SEARCH_RADIUS_KM if place_ids else None,
        "filter": "fuel/convenience/truck categories or name regex",
        "name_filter_regex": names,
        "candidate_count": len(rows),
        "elapsed_seconds": elapsed,
        "output_bytes": len(data),
        "output_sha256": hashlib.sha256(data).hexdigest(),
        "accepted_station_count": 0,
        "note": "Transfer bytes are not measured by DuckDB; output bytes are measured.",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
