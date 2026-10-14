"""Link CSV stations to pinned EPA UST facility names and site points."""

import hashlib
import json
import os

from app.stations.catalogs.epa_sources import OUTPUT_ROOT, SERVICE, STATE_NAMES
from app.stations.catalogs.epa_ust_site_catalog import (
    brand_similarity as brand_similarity,
)
from app.stations.catalogs.epa_ust_site_catalog import choose as choose
from app.stations.catalogs.epa_ust_site_catalog import (
    choose_chain_city as choose_chain_city,
)
from app.stations.catalogs.epa_ust_site_catalog import (
    choose_fuzzy_chain_city as choose_fuzzy_chain_city,
)
from app.stations.catalogs.epa_ust_site_catalog import choose_nearby as choose_nearby
from app.stations.catalogs.epa_ust_site_catalog import city_key as city_key
from app.stations.catalogs.epa_ust_site_catalog import (
    csv_chain_city_counts as csv_chain_city_counts,
)
from app.stations.catalogs.epa_ust_site_catalog import (
    facility_numbers as facility_numbers,
)
from app.stations.catalogs.epa_ust_site_catalog import load_state as load_state
from app.stations.catalogs.epa_ust_site_catalog import normalized as normalized
from app.stations.catalogs.epa_ust_site_catalog import (
    same_brand_prefix as same_brand_prefix,
)
from app.stations.catalogs.epa_ust_site_catalog import select as select
from app.stations.catalogs.epa_ust_site_catalog import state_index as state_index
from app.stations.catalogs.epa_ust_site_catalog import (
    usable_coordinate as usable_coordinate,
)
from app.stations.catalogs.epa_ust_site_catalog import (
    validate_catalog as validate_catalog,
)
from app.stations.catalogs.overture_identity import (
    CITY_PLACES,
    city_centers,
    source_stations,
)
from app.stations.catalogs.paths import SOURCE as SOURCE
from app.stations.catalogs.source_queue import build_queue

OUTPUT = OUTPUT_ROOT / "site_catalog.json"


def build_catalog() -> dict:
    queue = build_queue()
    existing = json.loads(OUTPUT.read_bytes()) if OUTPUT.exists() else None
    if existing is not None:
        validate_catalog(existing)
    eligible = {item["opis_id"] for item in queue["stations"]}
    if existing is not None:
        eligible.update(item["opis_id"] for item in existing["records"])
    stations = source_stations()
    csv_counts = csv_chain_city_counts(stations)
    centers = city_centers()
    records = []
    state_hashes = {}
    used_facilities: set[int] = set()
    for code in STATE_NAMES:
        rows, state_hashes[code] = load_state(code)
        indices = state_index(rows)
        for opis_id in sorted(eligible, key=int):
            station = stations[opis_id]
            if station["states"] != [code]:
                continue
            chosen = select(station, code, indices, centers, csv_counts)
            if chosen is None:
                continue
            row, csv_name, csv_city, rule, overlap, distance = chosen
            if row["OBJECTID"] in used_facilities:
                continue
            records.append(
                {
                    "opis_id": opis_id,
                    "state": code,
                    "csv_name": csv_name,
                    "csv_city": csv_city,
                    "rule": rule,
                    "road_number_overlap": overlap,
                    "city_distance_miles": distance,
                    "epa_object_id": row["OBJECTID"],
                    "epa_facility_id": row["Facility_ID"],
                    "epa_name": row["Name"],
                    "epa_address": row["Address"],
                    "epa_city": row["City"],
                    "epa_status": row["Facility_Status"],
                    "coordinate_source": row["Coordinate_Source"],
                    "address_match_type": row["Address_Match_Type"],
                    "latitude": row["Latitude"],
                    "longitude": row["Longitude"],
                    "site_id": f"epa-ust:{row['OBJECTID']}",
                }
            )
            used_facilities.add(row["OBJECTID"])
    if existing is not None and not {
        item["opis_id"] for item in existing["records"]
    } <= {item["opis_id"] for item in records}:
        raise ValueError("previous EPA links disappeared")
    document = {
        "source_sha256": queue["source_sha256"],
        "epa_source_url": SERVICE,
        "city_catalog_sha256": hashlib.sha256(CITY_PLACES.read_bytes()).hexdigest(),
        "state_hashes": state_hashes,
        "verified_count": len(records),
        "records": records,
    }
    validate_catalog(document)
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    os.replace(temporary, OUTPUT)
    return document


if __name__ == "__main__":
    print(f"{build_catalog()['verified_count']} EPA UST CSV site links")
