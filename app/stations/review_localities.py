import hashlib

from app.stations.catalogs.canadian_petro_sites import (
    validate as validate_canadian_petro_sites,
)
from app.stations.catalogs.canadian_pilot_sites import (
    validate as validate_canadian_pilot_sites,
)
from app.stations.catalogs.csv_coordinate_catalog import (
    validate_catalog as validate_csv_coordinate_catalog,
)
from app.stations.catalogs.epa_branch_fallback_sites import (
    validate_catalog as validate_epa_branch_fallback_sites,
)
from app.stations.catalogs.exit_coordinate_catalog import (
    validate_catalog as validate_exit_coordinate_catalog,
)
from app.stations.catalogs.exit_fuel_sites import (
    validate_catalog as validate_exit_fuel_sites,
)
from app.stations.catalogs.illinois_gasnwash_sites import (
    validate as validate_illinois_gasnwash_sites,
)
from app.stations.catalogs.ohio_7eleven_sites import (
    validate as validate_ohio_7eleven_sites,
)
from app.stations.catalogs.reviewed_branch_sites import (
    validate_catalog as validate_reviewed_branch_sites,
)
from app.stations.catalogs.reviewed_city_fuel_sites import (
    validate_catalog as validate_reviewed_city_fuel_sites,
)
from app.stations.review_context import ReviewContext


def load_reviewed_localities(context: ReviewContext) -> None:
    root, source_hash = context.root, context.source_hash
    reviews, verified_ids, input_hashes = (
        context.reviews,
        context.verified_ids,
        context.input_hashes,
    )
    ohio_7eleven = validate_ohio_7eleven_sites(root)
    for record in ohio_7eleven["records"]:
        opis_id = record["opis_id"]
        if opis_id in reviews:
            raise ValueError(f"Turnpike site conflicts for {opis_id}")
        reviews[opis_id] = {
            "status": "site_verified",
            "site_id": record["site_id"],
            "overture_id": None,
            "latitude": record["latitude"],
            "longitude": record["longitude"],
        }
        verified_ids.add(opis_id)
    input_hashes["ohio_7eleven_sites"] = {
        "evidence_sha256": hashlib.sha256(
            (root / "data/review/ohio_7eleven_sites.json").read_bytes()
        ).hexdigest(),
        "tank_register_sha256": ohio_7eleven["tank_register_sha256"],
        "turnpike_sha256": ohio_7eleven["turnpike_sha256"],
        "verified_count": len(ohio_7eleven["records"]),
    }
    gasnwash = validate_illinois_gasnwash_sites()
    for record in gasnwash["records"]:
        opis_id = record["opis_id"]
        if opis_id in verified_ids:
            raise ValueError(f"Illinois Gas N Wash site conflicts for {opis_id}")
        reviews[opis_id] = {
            "status": "site_verified",
            "site_id": record["site_id"],
            "overture_id": record["overture_id"],
            "latitude": record["latitude"],
            "longitude": record["longitude"],
        }
        verified_ids.add(opis_id)
    input_hashes["illinois_gasnwash"] = {
        "evidence_sha256": hashlib.sha256(
            (root / "data/review/illinois_gasnwash_sites.json").read_bytes()
        ).hexdigest(),
        "verified_count": len(gasnwash["records"]),
    }
    canadian_pilot = validate_canadian_pilot_sites()
    if canadian_pilot["source_sha256"] != source_hash:
        raise ValueError("Canadian Pilot source differs")
    for record in canadian_pilot["records"]:
        opis_id = record["opis_id"]
        if opis_id in verified_ids:
            if reviews.get(opis_id, {}).get("site_id", "").startswith("pilot-family:"):
                continue
            raise ValueError(f"Canadian Pilot site conflicts for {opis_id}")
        reviews[opis_id] = {
            "status": "site_verified",
            "site_id": record["site_id"],
            "overture_id": record["overture_id"],
            "latitude": record["latitude"],
            "longitude": record["longitude"],
        }
        verified_ids.add(opis_id)
    input_hashes["canadian_pilot"] = {
        "evidence_sha256": canadian_pilot["evidence_sha256"],
        "overture_hashes": canadian_pilot["overture_hashes"],
        "verified_count": canadian_pilot["verified_count"],
    }
    canadian_petro = validate_canadian_petro_sites()
    if canadian_petro["source_sha256"] != source_hash:
        raise ValueError("Canadian Petro source differs")
    for record in canadian_petro["records"]:
        opis_id = record["opis_id"]
        if opis_id in verified_ids:
            raise ValueError(f"Canadian Petro site conflicts for {opis_id}")
        reviews[opis_id] = {
            "status": "site_verified",
            "site_id": record["site_id"],
            "overture_id": record["overture_id"],
            "latitude": record["latitude"],
            "longitude": record["longitude"],
        }
        verified_ids.add(opis_id)
    input_hashes["canadian_petro"] = {
        "evidence_sha256": canadian_petro["evidence_sha256"],
        "overture_hashes": canadian_petro["overture_hashes"],
        "verified_count": canadian_petro["verified_count"],
    }


def load_fallback_sites(context: ReviewContext) -> None:
    root, source_hash = context.root, context.source_hash
    reviews, verified_ids, input_hashes = (
        context.reviews,
        context.verified_ids,
        context.input_hashes,
    )
    coordinate_path = root / "var/station_locations/csv_coordinate_catalog.json"
    if coordinate_path.exists():
        catalog = validate_csv_coordinate_catalog(root, source_hash)
        coordinate_links_added = 0
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            if opis_id in verified_ids:
                continue  # A later physical-site link takes precedence.
            reviews[opis_id] = {
                "status": "csv_location_coordinate",
                "site_id": record["site_id"],
                "location_precision": "city_representative",
            }
            verified_ids.add(opis_id)
            coordinate_links_added += 1
        input_hashes["csv_coordinate_catalog"] = {
            "catalog_sha256": hashlib.sha256(coordinate_path.read_bytes()).hexdigest(),
            "linked_count": coordinate_links_added,
        }
    exit_path = root / "var/station_locations/exit_coordinate_catalog.json"
    if exit_path.exists():
        catalog = validate_exit_coordinate_catalog(root, source_hash)
        exit_links_added = 0
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            if reviews.get(opis_id, {}).get("status") != "csv_location_coordinate":
                continue  # Physical-site evidence takes precedence.
            reviews[opis_id] = {
                "status": "csv_exit_coordinate",
                "site_id": record["site_id"],
                "location_precision": "interstate_exit",
            }
            exit_links_added += 1
        input_hashes["exit_coordinate_catalog"] = {
            "catalog_sha256": hashlib.sha256(exit_path.read_bytes()).hexdigest(),
            "linked_count": exit_links_added,
        }
    fuel_path = root / "var/station_locations/exit_fuel_sites.json"
    if fuel_path.exists():
        catalog = validate_exit_fuel_sites(root, source_hash)
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            if reviews.get(opis_id, {}).get("status") != "csv_exit_coordinate":
                raise ValueError(f"reviewed exit fuel site has no exit link: {opis_id}")
            reviews[opis_id] = {
                "status": "site_verified",
                "site_id": record["site_id"],
                "overture_id": None,
            }
        input_hashes["exit_fuel_sites"] = {
            "catalog_sha256": hashlib.sha256(fuel_path.read_bytes()).hexdigest(),
            "linked_count": len(catalog["records"]),
        }
    city_fuel_path = root / "var/station_locations/reviewed_city_fuel_sites.json"
    if city_fuel_path.exists():
        catalog = validate_reviewed_city_fuel_sites(root, source_hash)
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            if reviews.get(opis_id, {}).get("status") != "csv_location_coordinate":
                raise ValueError(f"reviewed city fuel site has no city link: {opis_id}")
            reviews[opis_id] = {
                "status": "site_verified",
                "site_id": record["site_id"],
                "overture_id": record["site_id"].removeprefix("overture:"),
            }
        input_hashes["reviewed_city_fuel_sites"] = {
            "catalog_sha256": hashlib.sha256(city_fuel_path.read_bytes()).hexdigest(),
            "linked_count": len(catalog["records"]),
        }
    branch_path = root / "var/station_locations/reviewed_branch_sites.json"
    if branch_path.exists():
        catalog = validate_reviewed_branch_sites(root, source_hash)
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            if reviews.get(opis_id, {}).get("status") != "csv_location_coordinate":
                raise ValueError(f"reviewed branch site has no city link: {opis_id}")
            reviews[opis_id] = {
                "status": "site_verified",
                "site_id": record["site_id"],
                "overture_id": (
                    record["site_id"].removeprefix("overture:")
                    if record["site_id"].startswith("overture:")
                    else None
                ),
            }
        input_hashes["reviewed_branch_sites"] = {
            "catalog_sha256": hashlib.sha256(branch_path.read_bytes()).hexdigest(),
            "linked_count": len(catalog["records"]),
        }
    epa_branch_path = root / "var/station_locations/epa_branch_fallback_sites.json"
    if epa_branch_path.exists():
        catalog = validate_epa_branch_fallback_sites(root, source_hash)
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            if reviews.get(opis_id, {}).get("status") != "csv_location_coordinate":
                raise ValueError(f"EPA branch site has no city link: {opis_id}")
            reviews[opis_id] = {
                "status": "site_verified",
                "site_id": record["site_id"],
                "overture_id": None,
            }
        input_hashes["epa_branch_fallback_sites"] = {
            "catalog_sha256": hashlib.sha256(epa_branch_path.read_bytes()).hexdigest(),
            "linked_count": len(catalog["records"]),
        }
