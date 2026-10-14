import hashlib
import json
import os
from collections import defaultdict

from app.stations.catalogs.comdata_matching import (
    OUTPUT,
    OVERTURE,
    PDF,
    ROWS,
    choose_place,
    csv_state_name_ids,
    house_number,
    identity_name,
    plain,
    point_coordinates,
    unique_public_row,
    unique_public_state_row,
)
from app.stations.catalogs.comdata_validation import validate_catalog
from app.stations.catalogs.overture_identity import (
    CITY_PLACES,
    city_centers,
    nearby_city,
)
from app.stations.catalogs.source_queue import build_queue


def build() -> dict:
    queue = build_queue()
    rows_bytes = ROWS.read_bytes()
    source_rows = json.loads(rows_bytes)
    if source_rows["pdf_sha256"] != hashlib.sha256(
        PDF.read_bytes()
    ).hexdigest() or source_rows["row_count"] != len(source_rows["rows"]):
        raise ValueError("public truckstop row extract changed")
    by_locality: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_state_name: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in source_rows["rows"]:
        by_locality[(row["state"], plain(row["city"]))].append(row)
        by_state_name[(row["state"], identity_name(row["name"]))].append(row)
    prior = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else None
    eligible = {
        item["opis_id"]: item for item in queue["stations"] if item["country"] == "US"
    }
    if prior is not None:
        if prior["source_sha256"] != queue["source_sha256"]:
            raise ValueError("previous public-site catalog belongs to another CSV")
        for record in prior["records"]:
            eligible[record["opis_id"]] = record["csv_station"]
    centers = city_centers()
    state_name_ids = csv_state_name_ids()
    by_state: dict[str, list[tuple[dict, dict, bool]]] = defaultdict(list)
    for station in eligible.values():
        row = unique_public_row(station, by_locality)
        nearby_rule = row is None
        if nearby_rule:
            row = unique_public_state_row(station, by_state_name)
        if row is not None:
            by_state[row["state"]].append((station, row, nearby_rule))
    records = []
    state_hashes = {}
    for state, items in sorted(by_state.items()):
        path = OVERTURE / f"{state.lower()}_places.ndjson"
        manifest = json.loads((OVERTURE / f"{state.lower()}_manifest.json").read_text())
        data = path.read_bytes()
        state_hash = hashlib.sha256(data).hexdigest()
        if manifest["state"] != state or manifest["output_sha256"] != state_hash:
            raise ValueError(f"Overture state catalog changed: {state}")
        state_hashes[state] = state_hash
        index: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
        for place in map(json.loads, data.splitlines()):
            for address in place["addresses"] or []:
                house = house_number(address.get("freeform") or "")
                if address.get("region") == state and house:
                    index[(plain(address.get("locality") or ""), house)][
                        place["id"]
                    ] = place
                    index[("*", house)][place["id"]] = place
        for station, row, nearby_rule in items:
            house = house_number(row["address"])
            if house is None:
                continue
            same_locality = list(index.get((plain(row["city"]), house), {}).values())
            chosen = choose_place(row, same_locality)
            locality_alias = False
            if (
                chosen is None
                and not same_locality
                and state_name_ids.get((state, identity_name(row["name"])))
                == {station["opis_id"]}
            ):
                chosen = choose_place(
                    row, list(index.get(("*", house), {}).values()), locality_alias=True
                )
                locality_alias = chosen is not None
            if chosen is None:
                continue
            place, alternatives = chosen
            latitude, longitude = point_coordinates(place["point_wkt"])
            city_match = (
                nearby_city(
                    state,
                    station["cities"],
                    (latitude, longitude),
                    centers,
                    max_miles=10.0,
                )
                if nearby_rule or locality_alias
                else None
            )
            if (nearby_rule or locality_alias) and city_match is None:
                continue
            record = {
                "opis_id": station["opis_id"],
                "csv_station": station,
                "public_row": row,
                "overture_id": place["id"],
                "overture_alternatives": alternatives,
                "overture_name": place["name"],
                "overture_category": place["basic_category"],
                "overture_confidence": place["confidence"],
                "latitude": latitude,
                "longitude": longitude,
                "site_id": f"overture:{place['id']}",
                "mapped_locality_alias": locality_alias,
                "rule": (
                    "csv_and_unique_state_name_near_city_plus_street_address"
                    if nearby_rule
                    else "csv_and_public_name_city_plus_street_address"
                ),
            }
            if nearby_rule or locality_alias:
                record.update(
                    {
                        "csv_city": city_match[0],
                        "city_center_id": city_match[1],
                        "city_center_distance_miles": round(city_match[2], 3),
                    }
                )
            records.append(record)
    ids = [item["opis_id"] for item in records]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate public-site CSV IDs")
    if prior is not None and not {item["opis_id"] for item in prior["records"]} <= set(
        ids
    ):
        raise ValueError("existing public-site links disappeared")
    document = {
        "status": "cross_source_address_link",
        "source_sha256": queue["source_sha256"],
        "public_rows_sha256": hashlib.sha256(rows_bytes).hexdigest(),
        "public_pdf_sha256": source_rows["pdf_sha256"],
        "city_catalog_sha256": hashlib.sha256(CITY_PLACES.read_bytes()).hexdigest(),
        "overture_state_hashes": {
            state: state_hashes[state]
            for state in {record["public_row"]["state"] for record in records}
        },
        "verified_count": len(records),
        "records": sorted(records, key=lambda item: int(item["opis_id"])),
    }
    validate_catalog(document)
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, OUTPUT)
    return document
