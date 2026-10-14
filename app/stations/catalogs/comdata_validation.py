import csv
import hashlib
import json
from collections import defaultdict

from app.stations.catalogs.comdata_matching import (
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
from app.stations.catalogs.overture_identity import (
    CITY_PLACES,
    city_centers,
    nearby_city,
)
from app.stations.catalogs.paths import SOURCE


def validate_catalog(document: dict) -> None:
    rows_bytes = ROWS.read_bytes()
    source_rows = json.loads(rows_bytes)
    if (
        document["source_sha256"] != hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        or document["public_rows_sha256"] != hashlib.sha256(rows_bytes).hexdigest()
        or document["public_pdf_sha256"] != hashlib.sha256(PDF.read_bytes()).hexdigest()
        or document["public_pdf_sha256"] != source_rows["pdf_sha256"]
        or document["verified_count"] != len(document["records"])
        or document["status"] != "cross_source_address_link"
    ):
        raise ValueError("public-address catalog source differs")
    source: dict[str, list[dict[str, str]]] = defaultdict(list)
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            source[row["OPIS Truckstop ID"].strip()].append(row)
    public_rows = {
        (row["page"], row["line"], row["location_code"]): row
        for row in source_rows["rows"]
    }
    by_locality: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_state_name: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in source_rows["rows"]:
        by_locality[(row["state"], plain(row["city"]))].append(row)
        by_state_name[(row["state"], identity_name(row["name"]))].append(row)
    nearby_records = [
        record
        for record in document["records"]
        if record["rule"] == "csv_and_unique_state_name_near_city_plus_street_address"
        or record.get("mapped_locality_alias")
    ]
    if (
        nearby_records
        and document.get("city_catalog_sha256")
        != hashlib.sha256(CITY_PLACES.read_bytes()).hexdigest()
    ):
        raise ValueError("public-address city catalog changed")
    centers = city_centers() if nearby_records else {}
    state_name_ids = csv_state_name_ids()
    by_state: dict[str, list[dict]] = defaultdict(list)
    for record in document["records"]:
        by_state[record["public_row"]["state"]].append(record)
    ids: set[str] = set()
    for state, records in by_state.items():
        state_data = (OVERTURE / f"{state.lower()}_places.ndjson").read_bytes()
        state_hash = hashlib.sha256(state_data).hexdigest()
        manifest = json.loads((OVERTURE / f"{state.lower()}_manifest.json").read_text())
        if (
            document["overture_state_hashes"].get(state) != state_hash
            or manifest["output_sha256"] != state_hash
        ):
            raise ValueError(f"public-address Overture evidence changed: {state}")
        relevant = {
            (
                plain(record["public_row"]["city"]),
                house_number(record["public_row"]["address"]),
            )
            for record in records
        }
        alias_houses = {
            house_number(record["public_row"]["address"])
            for record in records
            if record.get("mapped_locality_alias")
        }
        index: dict[tuple[str, str | None], dict[str, dict]] = defaultdict(dict)
        for place in map(json.loads, state_data.splitlines()):
            for address in place["addresses"] or []:
                key = (
                    plain(address.get("locality") or ""),
                    house_number(address.get("freeform") or ""),
                )
                if address.get("region") == state and key in relevant:
                    index[key][place["id"]] = place
                if address.get("region") == state and key[1] in alias_houses:
                    index[("*", key[1])][place["id"]] = place
        for record in records:
            opis_id = record["opis_id"]
            source_group = source.get(opis_id, [])
            public_row = record["public_row"]
            source_copy = record["csv_station"]
            public_key = (
                public_row["page"],
                public_row["line"],
                public_row["location_code"],
            )
            if (
                opis_id in ids
                or not source_group
                or public_rows.get(public_key) != public_row
                or source_copy["states"]
                != sorted({row["State"].strip().upper() for row in source_group})
                or source_copy["cities"]
                != sorted({row["City"].strip() for row in source_group})
                or source_copy["names"]
                != sorted({row["Truckstop Name"].strip() for row in source_group})
                or identity_name(public_row["name"])
                not in {identity_name(name) for name in source_copy["names"]}
                or source_copy["states"] != [state]
                or record["rule"]
                not in {
                    "csv_and_public_name_city_plus_street_address",
                    "csv_and_unique_state_name_near_city_plus_street_address",
                }
            ):
                raise ValueError(f"public-address CSV identity differs: {opis_id}")
            if record["rule"] == "csv_and_public_name_city_plus_street_address":
                if (
                    plain(public_row["city"])
                    not in {plain(city) for city in source_copy["cities"]}
                    or unique_public_row(source_copy, by_locality) != public_row
                ):
                    raise ValueError(f"public-address city identity differs: {opis_id}")
            elif (
                plain(public_row["city"])
                in {plain(city) for city in source_copy["cities"]}
                or unique_public_row(source_copy, by_locality) is not None
                or unique_public_state_row(source_copy, by_state_name) != public_row
            ):
                raise ValueError(f"public-address state identity differs: {opis_id}")
            alias = record.get("mapped_locality_alias", False)
            key = (
                "*" if alias else plain(public_row["city"]),
                house_number(public_row["address"]),
            )
            chosen = choose_place(
                public_row, list(index.get(key, {}).values()), locality_alias=alias
            )
            if chosen is None:
                raise ValueError(
                    f"public-address Overture match disappeared: {opis_id}"
                )
            place, alternatives = chosen
            coordinates = point_coordinates(place["point_wkt"])
            if alias and (
                state_name_ids.get((state, identity_name(public_row["name"])))
                != {opis_id}
                or any(
                    plain(address.get("locality") or "") == plain(public_row["city"])
                    and address.get("region") == state
                    and house_number(address.get("freeform") or "")
                    == house_number(public_row["address"])
                    for candidate in index.get(key, {}).values()
                    for address in candidate["addresses"] or []
                )
            ):
                raise ValueError(f"public-address locality fallback differs: {opis_id}")
            if (
                alias
                or record["rule"]
                == "csv_and_unique_state_name_near_city_plus_street_address"
            ):
                city_match = nearby_city(
                    state, source_copy["cities"], coordinates, centers, max_miles=10.0
                )
                if (
                    city_match is None
                    or record["csv_city"] != city_match[0]
                    or record["city_center_id"] != city_match[1]
                    or record["city_center_distance_miles"] != round(city_match[2], 3)
                ):
                    raise ValueError(f"public-address nearby city differs: {opis_id}")
            if (
                place["id"] != record["overture_id"]
                or alternatives != record["overture_alternatives"]
                or coordinates != (record["latitude"], record["longitude"])
                or place["name"] != record["overture_name"]
                or place["basic_category"] != record["overture_category"]
                or place["confidence"] != record["overture_confidence"]
                or record["site_id"] != f"overture:{place['id']}"
            ):
                raise ValueError(f"public-address coordinate differs: {opis_id}")
            ids.add(opis_id)
    if set(document["overture_state_hashes"]) != set(by_state):
        raise ValueError("public-address state hashes differ")
