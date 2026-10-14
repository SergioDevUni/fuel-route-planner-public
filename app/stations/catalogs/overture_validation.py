import hashlib
import json
from collections import defaultdict

from app.stations.catalogs.overture_identity import (
    CITY_PLACES,
    OUTPUT_ROOT,
    RELEASE,
    city_centers,
    source_stations,
)
from app.stations.catalogs.overture_record_validation import (
    CatalogValidationContext,
    validate_chain,
    validate_exact_city,
    validate_identity,
    validate_long_branch,
    validate_nearby,
    validate_road_site,
)
from app.stations.catalogs.paths import SOURCE


def validate_catalog(document: dict) -> None:
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if document["source_sha256"] != source_hash or document["release"] != RELEASE:
        raise ValueError("exact-site catalog source or release changed")
    source = source_stations()
    nearby_records = [
        record
        for record in document["records"]
        if record["rule"]
        in {
            "unique_state_name_road_near_csv_city",
            "unique_state_name_near_csv_city",
            "unique_branch_name_near_csv_city",
        }
    ]
    if (
        nearby_records
        and document.get("city_catalog_sha256")
        != hashlib.sha256(CITY_PLACES.read_bytes()).hexdigest()
    ):
        raise ValueError("nearby-city evidence changed")
    centers = city_centers() if nearby_records else {}
    nearby_states = {record["state"] for record in nearby_records}
    state_places: dict[str, list[dict]] = {}
    long_branch_places: dict[str, dict[str, dict]] = {}
    road_site_places_by_city: dict[str, dict[str, dict[str, dict]]] = {}
    ids: set[str] = set()
    for state, expected_hash in document["state_catalogs"].items():
        manifest = json.loads(
            (OUTPUT_ROOT / f"{state.lower()}_manifest.json").read_text()
        )
        actual = hashlib.sha256(
            (OUTPUT_ROOT / f"{state.lower()}_places.ndjson").read_bytes()
        ).hexdigest()
        if (
            manifest["state"] != state
            or manifest["release"] != RELEASE
            or manifest["output_sha256"] != actual
            or actual != expected_hash
        ):
            raise ValueError(f"state candidate catalog changed: {state}")
        if state in nearby_states:
            state_places[state] = [
                json.loads(line)
                for line in (OUTPUT_ROOT / f"{state.lower()}_places.ndjson")
                .read_bytes()
                .splitlines()
            ]
        if any(
            record["state"] == state
            and record["rule"]
            in {
                "unique_long_branch_city",
                "unique_name_city_road",
                "unique_distinctive_name_city",
                "unique_csv_name_road_city",
                "unique_distinctive_name_local_site",
                "unique_chain_name_city",
                "unique_fuzzy_chain_city",
            }
            for record in document["records"]
        ):
            long_branch_places[state] = {
                place["id"]: place
                for place in (
                    json.loads(line)
                    for line in (OUTPUT_ROOT / f"{state.lower()}_places.ndjson")
                    .read_bytes()
                    .splitlines()
                )
            }
            if any(
                record["state"] == state
                and record["rule"]
                in {
                    "unique_csv_name_road_city",
                    "unique_distinctive_name_local_site",
                    "unique_chain_name_city",
                    "unique_fuzzy_chain_city",
                }
                for record in document["records"]
            ):
                city_places: dict[str, dict[str, dict]] = defaultdict(dict)
                for place in long_branch_places[state].values():
                    for address in place["addresses"] or []:
                        if (
                            address.get("region") == state
                            and address.get("country") == "US"
                        ):
                            city = (address.get("locality") or "").casefold().strip()
                            if city:
                                city_places[city][place["id"]] = place
                road_site_places_by_city[state] = city_places
    context = CatalogValidationContext(
        document,
        source,
        centers,
        state_places,
        long_branch_places,
        road_site_places_by_city,
        ids,
    )
    for record in document["records"]:
        for validate in (
            validate_identity,
            validate_nearby,
            validate_long_branch,
            validate_road_site,
            validate_chain,
            validate_exact_city,
        ):
            validate(record, context)
        ids.add(record["opis_id"])
    if len(ids) != document["verified_count"]:
        raise ValueError("exact-site catalog count differs")
