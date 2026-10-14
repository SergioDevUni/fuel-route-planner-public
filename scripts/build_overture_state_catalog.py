"""Prepare a state-scoped Overture place candidate catalog; accepts no station match."""

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import duckdb
from shapely.geometry import shape

from app.stations.catalogs.paths import OVERTURE_RELEASE as RELEASE
from app.stations.catalogs.paths import OVERTURE_ROOT as OUTPUT_ROOT
from app.stations.catalogs.paths import OVERTURE_SOURCE as SOURCE

ROOT = Path(__file__).resolve().parents[1]
BOUNDS = ROOT / "data/prepared/locations/states_coarse.geojson"

FUEL_CATEGORIES = ("gas_station", "convenience_store", "truck_stop")
# A category-only query misses travel centers and some operator-labelled stores.
# This is a recall supplement, not a claim that every listed site is present.
NAME_HINT = (
    r"(?i)(fuel|gas|petrol|truck|travel|service station|road ranger|"
    r"pilot|flying j|one9|love.s|speedway|circle k|quiktrip|kwik trip|"
    r"casey.s|wawa|sheel|chevron|shell|exxon|mobil|bp|sunoco)"
)


def state_search_boxes(code: str) -> list[tuple[float, float, float, float]]:
    document = json.loads(BOUNDS.read_text(encoding="utf-8"))
    geometries = [
        shape(item["geometry"]) for item in document["states"] if item["code"] == code
    ]
    if len(geometries) != 1:
        raise ValueError(f"state boundary missing or duplicated: {code}")
    west, south, east, north = geometries[0].bounds
    if east - west <= 90:
        return [(west, south, east, north)]
    geometry = geometries[0]
    if not hasattr(geometry, "geoms"):
        raise ValueError(f"{code} antimeridian geometry cannot be split")
    groups = ([], [])
    for polygon in geometry.geoms:
        groups[0 if polygon.centroid.x < 0 else 1].append(polygon.bounds)
    if not all(groups):
        raise ValueError(f"{code} antimeridian split has an empty side")
    boxes = [
        (
            min(bounds[0] for bounds in group),
            min(bounds[1] for bounds in group),
            max(bounds[2] for bounds in group),
            max(bounds[3] for bounds in group),
        )
        for group in groups
    ]
    if any(box[2] - box[0] > 90 for box in boxes):
        raise ValueError(f"{code} antimeridian split remains too broad")
    return boxes


def prepare_state(code: str) -> dict:
    code = code.upper()
    if not re.fullmatch(r"[A-Z]{2}", code):
        raise ValueError("state must be a two-letter code")
    boxes = state_search_boxes(code)
    connection = duckdb.connect()
    connection.execute("LOAD httpfs")
    box_filter = " OR ".join(
        "(bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ?)" for _ in boxes
    )
    query = f"""
        SELECT id, names.primary AS name, addresses, basic_category,
               confidence, operating_status, sources,
               ST_AsText(geometry) AS point_wkt
        FROM read_parquet('{SOURCE}', hive_partitioning=1)
        WHERE ({box_filter})
          AND len(list_filter(
              addresses,
              a -> upper(coalesce(a.region, '')) = ?
                   AND upper(coalesce(a.country, '')) = 'US'
          )) > 0
          AND (
              basic_category IN ('gas_station', 'convenience_store', 'truck_stop')
              OR regexp_matches(coalesce(names.primary, ''), ?)
          )
        ORDER BY id
    """
    started = time.monotonic()
    parameters = [
        value
        for west, south, east, north in boxes
        for value in (west, east, south, north)
    ]
    rows = connection.execute(query, [*parameters, code, NAME_HINT]).fetchall()
    elapsed = round(time.monotonic() - started, 3)
    output = OUTPUT_ROOT / f"{code.lower()}_places.ndjson"
    manifest_path = OUTPUT_ROOT / f"{code.lower()}_manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for (
            place_id,
            name,
            addresses,
            category,
            confidence,
            status,
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
                        "operating_status": status,
                        "sources": sources,
                        "point_wkt": point,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
    data = output.read_bytes()
    manifest = {
        "status": "candidate_places_only",
        "release": RELEASE,
        "source": SOURCE,
        "state": code,
        "bbox_west_south_east_north": boxes,
        "filter": "state address plus fuel categories or broad fuel/operator name",
        "name_hint": NAME_HINT,
        "candidate_count": len(rows),
        "output_bytes": len(data),
        "output_sha256": hashlib.sha256(data).hexdigest(),
        "elapsed_seconds": elapsed,
        "accepted_station_count": 0,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True)
    arguments = parser.parse_args()
    print(json.dumps(prepare_state(arguments.state), indent=2))


if __name__ == "__main__":
    main()
