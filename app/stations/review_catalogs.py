import hashlib
import json

from app.stations.catalogs.caseys_site_catalog import (
    validate_catalog as validate_caseys_catalog,
)
from app.stations.catalogs.comdata_validation import (
    validate_catalog as validate_comdata_catalog,
)
from app.stations.catalogs.epa_ust_site_catalog import (
    validate_catalog as validate_epa_ust_catalog,
)
from app.stations.catalogs.kwiktrip_site_catalog import (
    validate_catalog as validate_kwiktrip_catalog,
)
from app.stations.catalogs.quarles_epa_site_catalog import (
    validate_catalog as validate_quarles_epa_catalog,
)
from app.stations.catalogs.quarles_site_catalog import (
    validate_catalog as validate_quarles_catalog,
)
from app.stations.catalogs.quiktrip_site_catalog import (
    validate_catalog as validate_quiktrip_catalog,
)
from app.stations.review_context import ReviewContext


def load_operator_catalogs(context: ReviewContext) -> None:
    root, source_hash = context.root, context.source_hash
    reviews, verified_ids, input_hashes = (
        context.reviews,
        context.verified_ids,
        context.input_hashes,
    )
    kwik_path = root / "var/operator_review/kwiktrip_site_catalog.json"
    if kwik_path.exists():
        catalog_bytes = kwik_path.read_bytes()
        catalog = json.loads(catalog_bytes)
        workbook_bytes = (root / "var/operator_review/kwiktrip_stores.csv").read_bytes()
        validate_kwiktrip_catalog(catalog, workbook_bytes)
        if catalog["source_sha256"] != source_hash:
            raise ValueError("Kwik Trip site catalog source differs")
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            if opis_id in reviews:
                raise ValueError(f"Kwik Trip site conflicts for {opis_id}")
            reviews[opis_id] = {
                "status": "operator_coordinate",
                "site_id": record["site_id"],
                "overture_id": None,
                "latitude": record["latitude"],
                "longitude": record["longitude"],
            }
            verified_ids.add(opis_id)
        input_hashes["kwiktrip_operator"] = {
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "workbook_sha256": catalog["workbook_sha256"],
            "verified_count": catalog["verified_count"],
            "unresolved_count": catalog["unresolved_count"],
            "shared_site_groups": catalog["shared_site_groups"],
        }
    quik_path = root / "var/operator_review/quiktrip_site_catalog.json"
    if quik_path.exists():
        catalog_bytes = quik_path.read_bytes()
        catalog = json.loads(catalog_bytes)
        pages_bytes = (root / "var/operator_review/quiktrip_pages.json").read_bytes()
        validate_quiktrip_catalog(catalog, json.loads(pages_bytes))
        if catalog["source_sha256"] != source_hash:
            raise ValueError("QuikTrip site catalog source differs")
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            if opis_id in reviews:
                raise ValueError(f"QuikTrip site conflicts for {opis_id}")
            reviews[opis_id] = {
                "status": "operator_coordinate",
                "site_id": record["site_id"],
                "overture_id": None,
                "latitude": record["latitude"],
                "longitude": record["longitude"],
            }
            verified_ids.add(opis_id)
        input_hashes["quiktrip_operator"] = {
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "pages_sha256": catalog["pages_sha256"],
            "sitemap_sha256": catalog["sitemap_sha256"],
            "verified_count": catalog["verified_count"],
            "unresolved_count": catalog["unresolved_count"],
        }
    comdata_path = root / "var/operator_review/comdata_site_catalog.json"
    if comdata_path.exists():
        catalog_bytes = comdata_path.read_bytes()
        catalog = json.loads(catalog_bytes)
        validate_comdata_catalog(catalog)
        if catalog["source_sha256"] != source_hash:
            raise ValueError("Comdata site catalog source differs")
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            if opis_id in reviews:
                raise ValueError(f"Comdata site conflicts for {opis_id}")
            reviews[opis_id] = {
                "status": "site_verified",
                "site_id": record["site_id"],
                "overture_id": record["overture_id"],
                "latitude": record["latitude"],
                "longitude": record["longitude"],
            }
            verified_ids.add(opis_id)
        input_hashes["comdata_public_address"] = {
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "pdf_sha256": catalog["public_pdf_sha256"],
            "overture_state_hashes": catalog["overture_state_hashes"],
            "verified_count": catalog["verified_count"],
        }
    caseys_path = root / "var/operator_review/caseys_site_catalog.json"
    if caseys_path.exists():
        catalog_bytes = caseys_path.read_bytes()
        catalog = json.loads(catalog_bytes)
        validate_caseys_catalog(catalog)
        if catalog["source_sha256"] != source_hash:
            raise ValueError("Casey site catalog source differs")
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            if opis_id in reviews:
                raise ValueError(f"Casey site conflicts for {opis_id}")
            reviews[opis_id] = {
                "status": "site_verified",
                "site_id": record["site_id"],
                "overture_id": record["overture_id"],
                "latitude": record["latitude"],
                "longitude": record["longitude"],
            }
            verified_ids.add(opis_id)
        input_hashes["caseys_official_address"] = {
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "official_urls_sha256": catalog["official_urls_sha256"],
            "overture_state_hashes": catalog["overture_state_hashes"],
            "verified_count": catalog["verified_count"],
        }
    quarles_path = root / "var/operator_review/quarles_site_catalog.json"
    if quarles_path.exists():
        catalog_bytes = quarles_path.read_bytes()
        catalog = json.loads(catalog_bytes)
        validate_quarles_catalog(catalog)
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            if opis_id in reviews:
                raise ValueError(f"Quarles site conflicts for {opis_id}")
            reviews[opis_id] = {
                "status": "site_verified",
                "site_id": record["site_id"],
                "overture_id": record["overture_id"],
                "latitude": record["latitude"],
                "longitude": record["longitude"],
            }
            verified_ids.add(opis_id)
        input_hashes["quarles_public_address"] = {
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "guide_sha256": catalog["guide_sha256"],
            "overture_state_hashes": catalog["overture_state_hashes"],
            "verified_count": catalog["verified_count"],
        }
    quarles_epa_path = root / "var/operator_review/quarles_epa_site_catalog.json"
    if quarles_epa_path.exists():
        catalog_bytes = quarles_epa_path.read_bytes()
        catalog = json.loads(catalog_bytes)
        validate_quarles_epa_catalog(catalog)
        if catalog["source_sha256"] != source_hash:
            raise ValueError("Quarles EPA site catalog source differs")
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            if opis_id in verified_ids:
                raise ValueError(f"Quarles EPA site conflicts for {opis_id}")
            reviews[opis_id] = {
                "status": "site_verified",
                "site_id": record["site_id"],
                "overture_id": None,
                "latitude": record["latitude"],
                "longitude": record["longitude"],
            }
            verified_ids.add(opis_id)
        input_hashes["quarles_epa_public_facility"] = {
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "guide_sha256": catalog["guide_sha256"],
            "epa_state_hashes": catalog["epa_state_hashes"],
            "verified_count": catalog["verified_count"],
        }


def load_epa(context: ReviewContext) -> None:
    root = context.root
    reviews, verified_ids, input_hashes = (
        context.reviews,
        context.verified_ids,
        context.input_hashes,
    )
    epa_path = root / "var/epa_ust/site_catalog.json"
    if epa_path.exists():
        epa_bytes = epa_path.read_bytes()
        epa = json.loads(epa_bytes)
        validate_epa_ust_catalog(epa)
        for record in epa["records"]:
            opis_id = record["opis_id"]
            if opis_id in verified_ids:
                if (
                    reviews.get(opis_id, {})
                    .get("site_id", "")
                    .startswith(("ta:", "pilot-family:"))
                ):
                    continue
                raise ValueError(f"EPA UST site conflicts for {opis_id}")
            reviews[opis_id] = {
                "status": "site_verified",
                "site_id": record["site_id"],
                "overture_id": None,
                "latitude": record["latitude"],
                "longitude": record["longitude"],
            }
            verified_ids.add(opis_id)
        input_hashes["epa_ust"] = {
            "catalog_sha256": hashlib.sha256(epa_bytes).hexdigest(),
            "state_hashes": epa["state_hashes"],
            "verified_count": epa["verified_count"],
        }
