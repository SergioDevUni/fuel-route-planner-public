"""Download one EPA UST Finder state facility extract for offline CSV matching."""

import argparse
import hashlib
import json
import os

import httpx

from app.stations.catalogs.epa_sources import OUTPUT_ROOT as OUTPUT_ROOT
from app.stations.catalogs.epa_sources import SERVICE as SERVICE
from app.stations.catalogs.epa_sources import STATE_NAMES as STATE_NAMES
from app.stations.catalogs.paths import ROOT as ROOT

FIELDS = (
    "OBJECTID,Facility_ID,Name,Address,City,State,Zip_Code,Latitude,Longitude,"
    "Coordinate_Source,Address_Match_Type,Facility_Status"
)
PAGE_SIZE = 2000


def fetch_state(code: str, name: str) -> dict:
    if not code.isalpha() or len(code) != 2 or not name.replace(" ", "").isalpha():
        raise ValueError("state code or name is invalid")
    code = code.upper()
    where = f"State = '{name}'"
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        metadata_response = client.get(SERVICE, params={"f": "json"})
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        count_response = client.get(
            f"{SERVICE}/query",
            params={"f": "json", "where": where, "returnCountOnly": "true"},
        )
        count_response.raise_for_status()
        count = count_response.json()["count"]
        records = []
        for offset in range(0, count, PAGE_SIZE):
            response = client.get(
                f"{SERVICE}/query",
                params={
                    "f": "json",
                    "where": where,
                    "outFields": FIELDS,
                    "returnGeometry": "false",
                    "orderByFields": "OBJECTID ASC",
                    "resultOffset": offset,
                    "resultRecordCount": PAGE_SIZE,
                },
            )
            response.raise_for_status()
            page = response.json()
            if "error" in page:
                raise ValueError(
                    f"EPA query failed at offset {offset}: {page['error']}"
                )
            features = page["features"]
            if len(features) != min(PAGE_SIZE, count - offset):
                raise ValueError(f"EPA page count changed at offset {offset}")
            records.extend(feature["attributes"] for feature in features)
            print(f"{code}: {len(records)}/{count}", flush=True)
    object_ids = [row["OBJECTID"] for row in records]
    if (
        len(records) != count
        or len(set(object_ids)) != count
        or object_ids != sorted(object_ids)
        or any(row["State"] != name for row in records)
    ):
        raise ValueError("EPA extract is incomplete or unordered")
    payload = b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
        for row in records
    )
    manifest = {
        "source_url": SERVICE,
        "state_code": code,
        "state_name": name,
        "source_editing_info": metadata.get("editingInfo"),
        "count": count,
        "first_object_id": object_ids[0] if object_ids else None,
        "last_object_id": object_ids[-1] if object_ids else None,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "fields": FIELDS,
    }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    data_path = OUTPUT_ROOT / f"{code.lower()}_facilities.ndjson"
    manifest_path = OUTPUT_ROOT / f"{code.lower()}_manifest.json"
    temporary_data = data_path.with_suffix(".ndjson.tmp")
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_data.write_bytes(payload)
    temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    os.replace(temporary_data, data_path)
    os.replace(temporary_manifest, manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("code", nargs="?", help="two-letter state or ALL")
    arguments = parser.parse_args()
    if arguments.code == "ALL":
        for code, name in STATE_NAMES.items():
            if (OUTPUT_ROOT / f"{code.lower()}_manifest.json").exists():
                continue
            print(fetch_state(code, name))
    elif arguments.code and arguments.code.upper() in STATE_NAMES:
        code = arguments.code.upper()
        print(fetch_state(code, STATE_NAMES[code]))
    else:
        parser.error("provide a supported state code or ALL")


if __name__ == "__main__":
    main()
