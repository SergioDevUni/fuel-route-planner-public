"""Build a hashed station snapshot with source and manual-review checkedance."""

import hashlib
import json
from pathlib import Path

from app.stations.accounting import REQUIRED_CSV_SITE_COVERAGE_PERCENT, SITE_IDENTIFIED
from app.stations.manual_review import SITE_PRECISION, apply_manual_review
from app.stations.review_context import REGIONS
from app.stations.reviews import load_reviews


def _read_sites(root: Path) -> dict[str, dict]:
    sites: dict[str, dict] = {}
    for region in REGIONS:
        document = json.loads(
            (root / f"var/pilot/{region}_site_verified.json").read_text()
        )
        for item in document["stations"]:
            if item["site_status"] != "verified":
                continue
            sites[item["opis_id"]] = item
    document = json.loads(
        (root / "var/operator_review/loves_verified.json").read_text()
    )
    for item in document["records"]:
        sites[item["opis_id"]] = item
    catalog_path = root / "var/operator_review/loves_site_catalog.json"
    if catalog_path.exists():
        catalog = json.loads(catalog_path.read_text())
        for item in catalog["records"]:
            previous = sites.get(item["opis_id"])
            if previous is not None:
                continue  # Retain the prior reviewed site coordinate.
            sites[item["opis_id"]] = item
    case = json.loads((root / "data/review/green_river_sites.json").read_text())
    by_site = {item["site_id"]: item for item in case["sites"]}
    for link in case["source_links"]:
        sites[link["opis_id"]] = by_site[link["site_id"]]
    pilot_path = root / "var/operator_review/pilot_site_catalog.json"
    if pilot_path.exists():
        pilot = json.loads(pilot_path.read_text())
        for item in pilot["records"]:
            if item["country"] == "US" and item["opis_id"] not in sites:
                sites[item["opis_id"]] = item
    ta_path = root / "var/operator_review/ta_site_catalog.json"
    if ta_path.exists():
        ta = json.loads(ta_path.read_text())
        for item in ta["records"]:
            if item["opis_id"] not in sites:
                sites[item["opis_id"]] = item
    public_path = root / "var/overture_states/de_public_verified.json"
    if public_path.exists():
        document = json.loads(public_path.read_text())
        for item in document["records"]:
            if item["opis_id"] in sites:
                raise ValueError(f"duplicate Delaware site {item['opis_id']}")
            sites[item["opis_id"]] = item
    exact_path = root / "var/overture_states/exact_sites.json"
    if exact_path.exists():
        document = json.loads(exact_path.read_text())
        for item in document["records"]:
            if item["opis_id"] in sites:
                if str(sites[item["opis_id"]].get("site_id", "")).startswith(
                    ("ta:", "pilot-family:")
                ):
                    continue
                raise ValueError(f"duplicate exact Overture site {item['opis_id']}")
            sites[item["opis_id"]] = item
    kwik_path = root / "var/operator_review/kwiktrip_site_catalog.json"
    if kwik_path.exists():
        document = json.loads(kwik_path.read_text())
        for item in document["records"]:
            if item["opis_id"] in sites:
                raise ValueError(f"duplicate Kwik Trip site {item['opis_id']}")
            sites[item["opis_id"]] = item
    quik_path = root / "var/operator_review/quiktrip_site_catalog.json"
    if quik_path.exists():
        document = json.loads(quik_path.read_text())
        for item in document["records"]:
            if item["opis_id"] in sites:
                raise ValueError(f"duplicate QuikTrip site {item['opis_id']}")
            sites[item["opis_id"]] = item
    comdata_path = root / "var/operator_review/comdata_site_catalog.json"
    if comdata_path.exists():
        document = json.loads(comdata_path.read_text())
        for item in document["records"]:
            if item["opis_id"] in sites:
                raise ValueError(f"duplicate public-address site {item['opis_id']}")
            sites[item["opis_id"]] = item
    caseys_path = root / "var/operator_review/caseys_site_catalog.json"
    if caseys_path.exists():
        document = json.loads(caseys_path.read_text())
        for item in document["records"]:
            if item["opis_id"] in sites:
                raise ValueError(f"duplicate Casey site {item['opis_id']}")
            sites[item["opis_id"]] = item
    quarles_path = root / "var/operator_review/quarles_site_catalog.json"
    if quarles_path.exists():
        document = json.loads(quarles_path.read_text())
        for item in document["records"]:
            if item["opis_id"] in sites:
                raise ValueError(f"duplicate Quarles site {item['opis_id']}")
            sites[item["opis_id"]] = item
    quarles_epa_path = root / "var/operator_review/quarles_epa_site_catalog.json"
    if quarles_epa_path.exists():
        document = json.loads(quarles_epa_path.read_text())
        for item in document["records"]:
            if item["opis_id"] in sites:
                raise ValueError(f"duplicate Quarles EPA site {item['opis_id']}")
            sites[item["opis_id"]] = item
    ohio_7eleven = json.loads(
        (root / "data/review/ohio_7eleven_sites.json").read_text()
    )
    for item in ohio_7eleven["records"]:
        if item["opis_id"] in sites:
            raise ValueError(f"duplicate Ohio 7-Eleven site {item['opis_id']}")
        sites[item["opis_id"]] = item
    gasnwash = json.loads(
        (root / "data/review/illinois_gasnwash_sites.json").read_text()
    )
    for item in gasnwash["records"]:
        if item["opis_id"] in sites:
            raise ValueError(f"duplicate Illinois Gas N Wash site {item['opis_id']}")
        sites[item["opis_id"]] = item
    epa_path = root / "var/epa_ust/site_catalog.json"
    if epa_path.exists():
        epa = json.loads(epa_path.read_text())
        for item in epa["records"]:
            if item["opis_id"] in sites:
                if str(sites[item["opis_id"]].get("site_id", "")).startswith(
                    ("ta:", "pilot-family:")
                ):
                    continue
                raise ValueError(f"duplicate EPA UST site {item['opis_id']}")
            sites[item["opis_id"]] = item
    coordinate_path = root / "var/station_locations/csv_coordinate_catalog.json"
    if coordinate_path.exists():
        catalog = json.loads(coordinate_path.read_text())
        for item in catalog["records"]:
            if item["country"] == "US" and item["opis_id"] not in sites:
                sites[item["opis_id"]] = item
    exit_path = root / "var/station_locations/exit_coordinate_catalog.json"
    if exit_path.exists():
        catalog = json.loads(exit_path.read_text())
        for item in catalog["records"]:
            previous = sites.get(item["opis_id"])
            if previous and previous.get("location_precision") == "city_representative":
                sites[item["opis_id"]] = item
    fuel_path = root / "var/station_locations/exit_fuel_sites.json"
    if fuel_path.exists():
        catalog = json.loads(fuel_path.read_text())
        for item in catalog["records"]:
            previous = sites.get(item["opis_id"])
            if previous and previous.get("location_precision") == "interstate_exit":
                sites[item["opis_id"]] = item
    city_fuel_path = root / "var/station_locations/reviewed_city_fuel_sites.json"
    if city_fuel_path.exists():
        catalog = json.loads(city_fuel_path.read_text())
        for item in catalog["records"]:
            previous = sites.get(item["opis_id"])
            if previous and previous.get("location_precision") == "city_representative":
                sites[item["opis_id"]] = item
    branch_path = root / "var/station_locations/reviewed_branch_sites.json"
    if branch_path.exists():
        catalog = json.loads(branch_path.read_text())
        for item in catalog["records"]:
            previous = sites.get(item["opis_id"])
            if previous and previous.get("location_precision") == "city_representative":
                sites[item["opis_id"]] = item
    epa_branch_path = root / "var/station_locations/epa_branch_fallback_sites.json"
    if epa_branch_path.exists():
        catalog = json.loads(epa_branch_path.read_text())
        for item in catalog["records"]:
            previous = sites.get(item["opis_id"])
            if previous and previous.get("location_precision") == "city_representative":
                sites[item["opis_id"]] = item
    return sites


def build_candidate_snapshot(
    root: Path, source_hash: str, stations: list[dict]
) -> tuple[bytes, dict]:
    """Require reviewed identity evidence and include each linked CSV ID once."""
    profile = json.loads((root / "data/audit/source-profile.json").read_text())
    if (
        profile["sha256"] != source_hash
        or profile["us_unique_station_ids"] != len(stations)
        or profile["unique_station_ids"]
        != profile["us_unique_station_ids"] + profile["non_us_unique_station_ids"]
    ):
        raise ValueError("source profile differs from imported price dataset")
    reviews, verified_ids, inputs = load_reviews(root, source_hash)
    sites = _read_sites(root)
    by_id = {station["opis_id"]: station for station in stations}
    us_verified_ids = verified_ids & set(by_id)
    non_us_linked_count = len(verified_ids - set(by_id))
    if non_us_linked_count > profile["non_us_unique_station_ids"]:
        raise ValueError("verified non-US identities exceed audited CSV count")
    if set(sites) != us_verified_ids:
        raise ValueError("physical site records differ from verified CSV identities")
    if len(by_id) != len(stations):
        raise ValueError("candidate identities differ from imported price data")
    records = []
    for opis_id in sorted(sites, key=int):
        station = by_id[opis_id]
        site = sites[opis_id]
        review = reviews[opis_id]
        if review["status"] not in {
            "site_verified",
            "operator_coordinate",
            "csv_store_site_linked",
            "csv_location_coordinate",
            "csv_exit_coordinate",
        }:
            raise ValueError(f"pending_check candidate identity {opis_id}")
        latitude = site["latitude"]
        longitude = site["longitude"]
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError(f"invalid candidate coordinates for {opis_id}")
        records.append(
            {
                "station_id": opis_id,
                "name": station["display_name"],
                "address": station["address"],
                "city": station["city"],
                "state": station["state"],
                "latitude": latitude,
                "longitude": longitude,
                "price_per_gallon": station["selected_price_text"],
                "site_id": review.get("site_id") or review.get("overture_id"),
                "location_precision": (
                    "city_representative"
                    if review["status"] == "csv_location_coordinate"
                    else "interstate_exit"
                    if review["status"] == "csv_exit_coordinate"
                    else "site_candidate"
                ),
            }
        )
    manual_review = apply_manual_review(root, source_hash, records)
    payload = (
        json.dumps(records, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    manifest = {
        "schema_version": 1,
        "status": (
            "complete_csv_location_snapshot"
            if len(records) + non_us_linked_count == profile["unique_station_ids"]
            else "partial_candidate_snapshot"
        ),
        "source_sha256": source_hash,
        "candidate_count": len(records),
        "total_us_station_ids": len(stations),
        "candidate_coverage_percent": round(100 * len(records) / len(stations), 4),
        "total_csv_station_ids": profile["unique_station_ids"],
        "total_non_us_station_ids": profile["non_us_unique_station_ids"],
        "linked_csv_station_ids": len(records) + non_us_linked_count,
        "site_candidate_station_ids": sum(
            reviews[opis_id]["status"] in SITE_IDENTIFIED for opis_id in verified_ids
        ),
        "city_representative_station_ids": sum(
            reviews[opis_id]["status"] == "csv_location_coordinate"
            for opis_id in verified_ids
        ),
        "interstate_exit_station_ids": sum(
            reviews[opis_id]["status"] == "csv_exit_coordinate"
            for opis_id in verified_ids
        ),
        "csv_coverage_percent": round(
            100 * (len(records) + non_us_linked_count) / profile["unique_station_ids"],
            4,
        ),
        "non_us_linked_station_ids": non_us_linked_count,
        "required_coverage_percent": REQUIRED_CSV_SITE_COVERAGE_PERCENT,
        "candidate_sha256": hashlib.sha256(payload).hexdigest(),
        "inputs": inputs,
        "access_policy": "verify_selected_waypoints_on_trip_route",
    }
    if manual_review is not None:
        manifest["inputs"]["us_manual_review"] = manual_review
        manifest["fuel_station_site_us_ids"] = sum(
            item["location_precision"] == SITE_PRECISION for item in records
        )
        manifest["fuel_station_site_station_ids"] = manifest["fuel_station_site_us_ids"]
        manifest["location_precision_counts_us"] = {
            precision: sum(item["location_precision"] == precision for item in records)
            for precision in sorted({item["location_precision"] for item in records})
        }
        # These totals retain non-US source classifications. Reviewed US offers
        # now have the explicit site classification above.
        manifest["source_precision_counts"] = {
            key: manifest[key]
            for key in (
                "site_candidate_station_ids",
                "city_representative_station_ids",
                "interstate_exit_station_ids",
            )
        }
        manifest["city_representative_station_ids"] -= sum(
            item["source_location_precision"] == "city_representative"
            for item in records
        )
        manifest["interstate_exit_station_ids"] -= sum(
            item["source_location_precision"] == "interstate_exit" for item in records
        )
        manifest["site_candidate_station_ids"] -= sum(
            item["source_location_precision"] == "site_candidate" for item in records
        )
    return payload, manifest
