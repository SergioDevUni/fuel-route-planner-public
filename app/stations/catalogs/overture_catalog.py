import hashlib
import json
import os
import re
from collections import Counter, defaultdict

from app.stations.catalogs.overture_chain_rules import match_chain, match_fuzzy_chain
from app.stations.catalogs.overture_context import OvertureContext
from app.stations.catalogs.overture_identity import (
    CITY_PLACES,
    OUTPUT,
    OUTPUT_ROOT,
    RELEASE,
    city_centers,
    normalized,
    source_stations,
)
from app.stations.catalogs.overture_identity_rules import (
    match_city_road,
    match_exact_city,
    match_long_branch,
    match_state_road,
)
from app.stations.catalogs.overture_nearby_rules import (
    match_fuzzy_name,
    match_near_branch,
    match_state_near,
)
from app.stations.catalogs.overture_road_rules import match_csv_road, match_distinctive
from app.stations.catalogs.overture_validation import validate_catalog
from app.stations.catalogs.paths import ROOT
from app.stations.catalogs.source_queue import build_queue


def build() -> dict:
    queue = build_queue()
    existing = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else None
    if existing is not None:
        validate_catalog(existing)
    source = source_stations()
    eligible_ids = {row["opis_id"] for row in queue["stations"]}
    if existing is not None:
        eligible_ids.update(item["opis_id"] for item in existing["records"])
    centers = city_centers()
    occupied_sites = {
        item["site_id"]: item["station_id"]
        for item in json.loads(
            (ROOT / "var/station_candidates/candidates.json").read_text()
        )
    }
    records = []
    state_hashes = {}
    for manifest_path in sorted(OUTPUT_ROOT.glob("??_manifest.json")):
        manifest = json.loads(manifest_path.read_text())
        state = manifest["state"]
        data = (OUTPUT_ROOT / f"{state.lower()}_places.ndjson").read_bytes()
        if (
            manifest["release"] != RELEASE
            or hashlib.sha256(data).hexdigest() != manifest["output_sha256"]
        ):
            raise ValueError(f"state candidate catalog changed: {state}")
        places = [json.loads(line) for line in data.splitlines()]
        state_hashes[state] = manifest["output_sha256"]
        index: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
        branch_index: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
        branch_state_index: dict[str, dict[str, dict]] = defaultdict(dict)
        name_index: dict[str, dict[str, dict]] = defaultdict(dict)
        city_places: dict[str, dict[str, dict]] = defaultdict(dict)
        state_name_count = Counter(normalized(place["name"] or "") for place in places)
        for place in places:
            name = normalized(place["name"] or "")
            if not name:
                continue
            name_index[name][place["id"]] = place
            for number in re.findall(r"(?<!\d)\d{2,7}(?!\d)", place["name"] or ""):
                branch_state_index[number][place["id"]] = place
            for address in place["addresses"] or []:
                if address.get("region") == state and address.get("country") == "US":
                    city = (address.get("locality") or "").casefold().strip()
                    if city:
                        city_places[city][place["id"]] = place
                        index[(name, city)][place["id"]] = place
                        for number in re.findall(
                            r"(?<!\d)\d{2,7}(?!\d)", place["name"] or ""
                        ):
                            branch_index[(number, city)][place["id"]] = place
        context = OvertureContext(
            source,
            eligible_ids,
            state,
            records,
            centers,
            occupied_sites,
            index,
            branch_index,
            branch_state_index,
            name_index,
            city_places,
            state_name_count,
        )
        match_exact_city(context)
        match_state_road(context)
        match_long_branch(context)
        match_city_road(context)
        match_state_near(context)
        match_fuzzy_name(context)
        match_near_branch(context)
        match_csv_road(context)
        match_distinctive(context)
        match_chain(context)
        match_fuzzy_chain(context)

    used_ids = [record["opis_id"] for record in records]
    if len(set(used_ids)) != len(used_ids):
        raise ValueError("exact-site match repeats a CSV ID")
    if existing is not None and not {
        item["opis_id"] for item in existing["records"]
    } <= set(used_ids):
        raise ValueError("existing exact-site links disappeared")
    document = {
        "source_sha256": queue["source_sha256"],
        "city_catalog_sha256": hashlib.sha256(CITY_PLACES.read_bytes()).hexdigest(),
        "release": RELEASE,
        "status": "unique_exact_name_city",
        "state_catalogs": state_hashes,
        "verified_count": len(records),
        "records": records,
    }
    validate_catalog(document)
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, OUTPUT)
    return document
