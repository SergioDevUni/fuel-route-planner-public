"""Extract source-hashed Canadian fuel-place candidates by province."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import duckdb
import httpx

from app.stations.catalogs.paths import OVERTURE_RELEASE as RELEASE
from app.stations.catalogs.paths import OVERTURE_SOURCE as SOURCE

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "var/overture_canada"
BOUNDARY_QUERY = (
    "https://geo.statcan.gc.ca/geo_wa/rest/services/2021/"
    "Cartographic_boundary_files/MapServer/0/query"
)
PROVINCE_IDS = {
    "AB": "48",
    "BC": "59",
    "MB": "46",
    "NB": "13",
    "NS": "12",
    "ON": "35",
    "QC": "24",
    "YT": "60",
}
COARSE_FALLBACK_BBOX = {
    # Conservative extract windows; the CA/province address filter remains required.
    "AB": (-121.0, 48.0, -109.0, 61.0),
    "QC": (-80.0, 44.0, -57.0, 63.0),
    "YT": (-141.0, 59.0, -123.0, 70.0),
}
NAME_HINT = r"(?i)(petro|fuel|gas|truck|travel|shell|husky|esso|flying j|pilot)"


def province_extent(code: str) -> tuple[tuple[float, float, float, float], str]:
    try:
        response = httpx.get(
            BOUNDARY_QUERY,
            params={
                "where": f"PRUID='{PROVINCE_IDS[code]}'",
                "returnExtentOnly": "true",
                "outSR": "4326",
                "f": "json",
            },
            timeout=30,
        )
        response.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException):
        if code not in COARSE_FALLBACK_BBOX:
            raise
        bbox = COARSE_FALLBACK_BBOX[code]
        digest = hashlib.sha256(
            json.dumps(
                {"fallback_bbox": bbox, "province": code}, sort_keys=True
            ).encode()
        ).hexdigest()
        return bbox, digest
    document = response.json()
    extent = document["extent"]
    if extent["spatialReference"]["wkid"] != 4326:
        raise ValueError("Canadian province extent is not WGS84")
    bbox = (extent["xmin"], extent["ymin"], extent["xmax"], extent["ymax"])
    if not (-180 <= bbox[0] < bbox[2] <= 180 and -90 <= bbox[1] < bbox[3] <= 90):
        raise ValueError("Canadian province extent is invalid")
    canonical = json.dumps(document, sort_keys=True).encode()
    return bbox, hashlib.sha256(canonical).hexdigest()


def prepare_province(code: str) -> dict:
    code = code.upper()
    if code not in PROVINCE_IDS:
        raise ValueError(f"unsupported Canadian province: {code}")
    bbox, boundary_hash = province_extent(code)
    connection = duckdb.connect()
    connection.execute("LOAD httpfs")
    query = f"""
        SELECT id, names.primary AS name, addresses, basic_category,
               confidence, operating_status, sources,
               ST_AsText(geometry) AS point_wkt
        FROM read_parquet('{SOURCE}', hive_partitioning=1)
        WHERE bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ?
          AND len(list_filter(
              addresses,
              a -> upper(coalesce(a.region, '')) = ?
                   AND upper(coalesce(a.country, '')) = 'CA'
          )) > 0
          AND (
              basic_category IN ('gas_station', 'convenience_store', 'truck_stop')
              OR regexp_matches(coalesce(names.primary, ''), ?)
          )
        ORDER BY id
    """
    started = time.monotonic()
    rows = connection.execute(
        query, [bbox[0], bbox[2], bbox[1], bbox[3], code, NAME_HINT]
    ).fetchall()
    elapsed = round(time.monotonic() - started, 3)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_ROOT / f"{code.lower()}_places.ndjson"
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(
                json.dumps(
                    dict(
                        zip(
                            (
                                "id",
                                "name",
                                "addresses",
                                "basic_category",
                                "confidence",
                                "operating_status",
                                "sources",
                                "point_wkt",
                            ),
                            row,
                            strict=True,
                        )
                    ),
                    separators=(",", ":"),
                )
                + "\n"
            )
    data = output.read_bytes()
    manifest = {
        "status": "candidate_places_only",
        "release": RELEASE,
        "source": SOURCE,
        "boundary_query_url": BOUNDARY_QUERY,
        "boundary_pruid": PROVINCE_IDS[code],
        "boundary_response_sha256": boundary_hash,
        "province": code,
        "bbox_west_south_east_north": bbox,
        "name_hint": NAME_HINT,
        "candidate_count": len(rows),
        "elapsed_seconds": elapsed,
        "output_bytes": len(data),
        "output_sha256": hashlib.sha256(data).hexdigest(),
        "accepted_station_count": 0,
    }
    (OUTPUT_ROOT / f"{code.lower()}_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("province", choices=sorted(PROVINCE_IDS))
    arguments = parser.parse_args()
    result = prepare_province(arguments.province)
    print(result["province"], result["candidate_count"], result["elapsed_seconds"])


if __name__ == "__main__":
    main()
