import hashlib
import json

from app.stations.catalogs.green_river import verify_links, verify_source_rows
from app.stations.catalogs.loves_crosswalk import read_codes, reconcile
from app.stations.catalogs.pilot_site_catalog import source_branches, validate_catalog
from app.stations.catalogs.ta_site_catalog import read_workbook as read_ta_workbook
from app.stations.catalogs.ta_site_catalog import source_stations as ta_source_stations
from app.stations.catalogs.ta_site_catalog import (
    validate_catalog as validate_ta_catalog,
)
from app.stations.review_context import REGIONS, ReviewContext


def load_regional(context: ReviewContext) -> None:
    root, source_hash = context.root, context.source_hash
    reviews, verified_ids, input_hashes = (
        context.reviews,
        context.verified_ids,
        context.input_hashes,
    )
    for region in REGIONS:
        evidence_path = root / f"data/review/{region}_site_evidence.json"
        verified_path = root / f"var/pilot/{region}_site_verified.json"
        extract_path = root / f"var/pilot/{region}_overture_places.ndjson"
        extract_manifest_path = root / f"var/pilot/{region}_overture_manifest.json"
        evidence_bytes = evidence_path.read_bytes()
        verified_bytes = verified_path.read_bytes()
        evidence = json.loads(evidence_bytes)
        verified = json.loads(verified_bytes)
        extract_manifest = json.loads(extract_manifest_path.read_bytes())
        if verified["source_sha256"] != source_hash:
            raise ValueError(f"{region} review was built for another price source")
        if evidence["source_release"] != verified["overture_release"]:
            raise ValueError(f"{region} review and validated release differ")
        if hashlib.sha256(evidence_bytes).hexdigest() != verified.get(
            "review_evidence_sha256"
        ):
            raise ValueError(f"{region} validated review is stale")
        if (
            extract_manifest["release"] != verified["overture_release"]
            or extract_manifest["output_sha256"] != verified["overture_extract_sha256"]
            or hashlib.sha256(extract_path.read_bytes()).hexdigest()
            != extract_manifest["output_sha256"]
        ):
            raise ValueError(f"{region} Overture extract differs from validated review")
        if verified["routing_verified_count"] != 0:
            raise ValueError("routing evidence needs a separate acceptance gate")
        for record in evidence["records"]:
            opis_id = record["opis_id"]
            if opis_id in reviews:
                raise ValueError(f"duplicate review ID {opis_id}")
            reviews[opis_id] = record
        for station in verified["stations"]:
            opis_id = station["opis_id"]
            if opis_id in verified_ids:
                raise ValueError(f"duplicate verified ID {opis_id}")
            verified_ids.add(opis_id)
        input_hashes[region] = {
            "review_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            "validated_output_sha256": hashlib.sha256(verified_bytes).hexdigest(),
            "overture_release": verified["overture_release"],
            "overture_extract_sha256": verified["overture_extract_sha256"],
        }


def load_loves(context: ReviewContext) -> None:
    root, source_hash = context.root, context.source_hash
    reviews, verified_ids, input_hashes = (
        context.reviews,
        context.verified_ids,
        context.input_hashes,
    )
    loves_evidence_path = root / "data/review/loves_operator_evidence.json"
    loves_verified_path = root / "var/operator_review/loves_verified.json"
    loves_evidence_bytes = loves_evidence_path.read_bytes()
    loves_verified_bytes = loves_verified_path.read_bytes()
    loves_evidence = json.loads(loves_evidence_bytes)
    loves_verified = json.loads(loves_verified_bytes)
    if (
        loves_verified["source_sha256"] != source_hash
        or loves_verified["review_evidence_sha256"]
        != hashlib.sha256(loves_evidence_bytes).hexdigest()
        or loves_verified["routing_verified_count"] != 0
    ):
        raise ValueError("Love's operator validation is stale or route status changed")
    operator_ids = {item["opis_id"] for item in loves_verified["records"]}
    evidence_ids = {item["opis_id"] for item in loves_evidence["records"]}
    if (
        len(operator_ids) != len(loves_verified["records"])
        or operator_ids != evidence_ids
    ):
        raise ValueError("Love's operator review IDs differ")
    validated_by_id = {item["opis_id"]: item for item in loves_verified["records"]}
    for record in loves_evidence["records"]:
        opis_id = record["opis_id"]
        if opis_id in reviews or record["status"] != "operator_coordinate":
            raise ValueError(f"duplicate or invalid Love's review ID {opis_id}")
        validated = validated_by_id[opis_id]
        if (
            validated["site_status"] != "verified"
            or validated["routing_status"] != "pending_check"
            or validated["store_number"] != record["store_number"]
            or validated["evidence_url"] != record["evidence_url"]
            or (validated["latitude"], validated["longitude"])
            != (record["latitude"], record["longitude"])
        ):
            raise ValueError(f"Love's validated record differs for {opis_id}")
        reviews[opis_id] = record
        verified_ids.add(opis_id)
    input_hashes["loves_operator"] = {
        "review_sha256": hashlib.sha256(loves_evidence_bytes).hexdigest(),
        "validated_output_sha256": hashlib.sha256(loves_verified_bytes).hexdigest(),
        "verified_at_utc": loves_verified["verified_at_utc"],
    }
    queue_path = root / "var/operator_review/loves_queue.json"
    workbook_path = root / "var/operator_review/loves_station_codes.xlsx"
    crosswalk_path = root / "var/operator_review/loves_crosswalk.json"
    queue_bytes = queue_path.read_bytes()
    workbook_bytes = workbook_path.read_bytes()
    crosswalk_bytes = crosswalk_path.read_bytes()
    queue = json.loads(queue_bytes)
    crosswalk = json.loads(crosswalk_bytes)
    if (
        queue["source_sha256"] != source_hash
        or crosswalk["source_sha256"] != source_hash
        or crosswalk["queue_sha256"] != hashlib.sha256(queue_bytes).hexdigest()
        or crosswalk["workbook_sha256"] != hashlib.sha256(workbook_bytes).hexdigest()
        or crosswalk["stations"]
        != reconcile(queue["stations"], read_codes(workbook_path))
    ):
        raise ValueError("Love's station-code crosswalk is stale")
    for row in crosswalk["stations"]:
        opis_id = row["opis_id"]
        if opis_id in reviews:
            if row["status"] != "exact_operator_id":
                raise ValueError(f"operator workbook conflicts with reviewed {opis_id}")
            continue
        status = row["status"]
        if status == "exact_operator_id":
            review_status = "operator_id_linked"
        elif status == "operator_id_or_state_conflict":
            review_status = "operator_id_conflict"
        elif status == "operator_store_missing":
            review_status = "operator_store_missing"
        else:
            raise ValueError(f"unhandled operator status for {opis_id}: {status}")
        reviews[opis_id] = {"status": review_status, "overture_id": None}
    input_hashes["loves_crosswalk"] = {
        "queue_sha256": hashlib.sha256(queue_bytes).hexdigest(),
        "workbook_sha256": hashlib.sha256(workbook_bytes).hexdigest(),
        "crosswalk_sha256": hashlib.sha256(crosswalk_bytes).hexdigest(),
        "source_url": crosswalk["source_url"],
    }
    catalog_path = root / "var/operator_review/loves_site_catalog.json"
    if catalog_path.exists():
        catalog_bytes = catalog_path.read_bytes()
        catalog = json.loads(catalog_bytes)
        exact_rows = {
            row["opis_id"]: row
            for row in crosswalk["stations"]
            if row["status"] == "exact_operator_id"
        }
        if (
            catalog["status"] != "complete_attempt"
            or catalog["source_sha256"] != source_hash
            or catalog["crosswalk_sha256"]
            != hashlib.sha256(crosswalk_bytes).hexdigest()
            or catalog["exact_id_count"] != len(exact_rows)
            or catalog["attempted_count"] != len(exact_rows)
            or catalog["verified_count"] != len(catalog["records"])
            or catalog["unresolved_count"] != len(catalog["unresolved"])
            or catalog["verified_count"] + catalog["unresolved_count"]
            != len(exact_rows)
        ):
            raise ValueError("Love's site catalog is incomplete or stale")
        seen_ids: set[str] = set()
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            row = exact_rows.get(opis_id)
            if (
                row is None
                or opis_id in seen_ids
                or record["store_number"] != row["store_numbers"][0]
                or record["operator_state"] != row["source_state"]
                or record["site_status"] != "verified"
                or record["routing_status"] != "pending_check"
                or not record["evidence_url"].startswith(
                    "https://www.loves.com/locations/"
                )
                or len(record["page_sha256"]) != 64
                or not -90 <= record["latitude"] <= 90
                or not -180 <= record["longitude"] <= 180
            ):
                raise ValueError(f"Love's catalog identity differs for {opis_id}")
            seen_ids.add(opis_id)
            if opis_id in verified_ids:
                existing = reviews[opis_id]
                if existing["status"] == "site_verified":
                    continue  # Retain the independently reviewed mapped site.
                if existing["status"] != "operator_coordinate" or (
                    existing["latitude"],
                    existing["longitude"],
                ) != (record["latitude"], record["longitude"]):
                    raise ValueError(
                        f"Love's catalog conflicts with prior review {opis_id}"
                    )
                continue
            if reviews[opis_id]["status"] != "operator_id_linked":
                raise ValueError(
                    f"Love's catalog overrides unresolved review {opis_id}"
                )
            reviews[opis_id] = {
                "status": "operator_coordinate",
                "latitude": record["latitude"],
                "longitude": record["longitude"],
                "site_id": f"loves-store-{record['store_number']}",
                "overture_id": None,
            }
            verified_ids.add(opis_id)
        unresolved_ids = {item["opis_id"] for item in catalog["unresolved"]}
        if (
            len(unresolved_ids) != len(catalog["unresolved"])
            or unresolved_ids & seen_ids
            or unresolved_ids | seen_ids != set(exact_rows)
        ):
            raise ValueError("Love's catalog omits or duplicates an exact ID")
        input_hashes["loves_site_catalog"] = {
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "sitemap_sha256": catalog["sitemap_sha256"],
            "verified_count": len(seen_ids),
            "unresolved_count": len(unresolved_ids),
        }


def load_green_river(context: ReviewContext) -> None:
    root, source_hash = context.root, context.source_hash
    reviews, verified_ids, input_hashes = (
        context.reviews,
        context.verified_ids,
        context.input_hashes,
    )
    workbook_path = root / "var/operator_review/loves_station_codes.xlsx"
    workbook_bytes = workbook_path.read_bytes()
    case_path = root / "data/review/green_river_sites.json"
    verified_path = root / "var/operator_review/green_river_verified.json"
    case_bytes = case_path.read_bytes()
    verified_bytes = verified_path.read_bytes()
    case = json.loads(case_bytes)
    case_verified = json.loads(verified_bytes)
    if (
        hashlib.sha256(
            (root / "data/raw/fuel-prices-for-be-assessment.csv").read_bytes()
        ).hexdigest()
        != source_hash
    ):
        raise ValueError("active price source differs from Green River review input")
    verify_source_rows(case, root / "data/raw/fuel-prices-for-be-assessment.csv")
    verify_links(case, workbook_path)
    if (
        case_verified["source_sha256"] != source_hash
        or case_verified["review_evidence_sha256"]
        != hashlib.sha256(case_bytes).hexdigest()
        or case_verified["workbook_sha256"]
        != hashlib.sha256(workbook_bytes).hexdigest()
        or case_verified["physical_site_count"] != 2
        or case_verified["price_link_status"] != "csv_store_matched"
        or case_verified["route_ready_count"] != 0
        or case_verified["source_links"] != case["source_links"]
        or len(case_verified["sites"]) != len(case["sites"])
    ):
        raise ValueError("Green River site evidence is stale or price links changed")
    for site, validated in zip(case["sites"], case_verified["sites"], strict=True):
        if (
            any(
                validated[key] != site[key]
                for key in (
                    "site_id",
                    "store_number",
                    "latitude",
                    "longitude",
                    "operator_opis_ids",
                    "access_status",
                )
            )
            or len(validated["page_sha256"]) != 64
        ):
            raise ValueError("Green River validated site differs from case evidence")
    sites_by_id = {site["site_id"]: site for site in case["sites"]}
    for link in case["source_links"]:
        review = reviews.get(link["opis_id"])
        if review is None or review["status"] != "operator_id_conflict":
            raise ValueError("Green River price-link conflict missing from crosswalk")
        site = sites_by_id[link["site_id"]]
        reviews[link["opis_id"]] = {
            "status": "csv_store_site_linked",
            "site_id": site["site_id"],
            "latitude": site["latitude"],
            "longitude": site["longitude"],
            "operator_id_discrepancy": True,
            "overture_id": None,
        }
        verified_ids.add(link["opis_id"])
    input_hashes["green_river_case"] = {
        "review_sha256": hashlib.sha256(case_bytes).hexdigest(),
        "validated_output_sha256": hashlib.sha256(verified_bytes).hexdigest(),
        "physical_site_count": 2,
        "price_link_status": "csv_store_matched",
        "operator_id_discrepancy_count": 2,
    }


def load_pilot_ta(context: ReviewContext) -> None:
    root, source_hash = context.root, context.source_hash
    reviews, verified_ids, input_hashes = (
        context.reviews,
        context.verified_ids,
        context.input_hashes,
    )
    pilot_path = root / "var/operator_review/pilot_site_catalog.json"
    if pilot_path.exists():
        catalog_bytes = pilot_path.read_bytes()
        catalog = json.loads(catalog_bytes)
        source = source_branches(root / "data/raw/fuel-prices-for-be-assessment.csv")
        validate_catalog(catalog, source, source_hash)
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            existing = reviews.get(opis_id)
            if existing is not None:
                if opis_id in verified_ids:
                    continue  # Preserve the earlier independently reviewed site.
                raise ValueError(f"Pilot catalog conflicts with review for {opis_id}")
            reviews[opis_id] = {
                "status": "operator_coordinate",
                "latitude": record["latitude"],
                "longitude": record["longitude"],
                "site_id": record["site_id"],
                "overture_id": None,
            }
            verified_ids.add(opis_id)
        input_hashes["pilot_site_catalog"] = {
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "sitemap_sha256": catalog["sitemap_sha256"],
            "verified_count": catalog["verified_count"],
            "unresolved_count": catalog["unresolved_count"],
            "page_failure_count": len(catalog["page_failures"]),
        }
    ta_path = root / "var/operator_review/ta_site_catalog.json"
    if ta_path.exists():
        catalog_bytes = ta_path.read_bytes()
        catalog = json.loads(catalog_bytes)
        workbook_bytes = (root / "var/operator_review/ta_locations.xlsx").read_bytes()
        if catalog["workbook_sha256"] != hashlib.sha256(
            workbook_bytes
        ).hexdigest() or catalog["sites"] != read_ta_workbook(workbook_bytes):
            raise ValueError("TA workbook differs from site catalog")
        source = ta_source_stations(root / "data/raw/fuel-prices-for-be-assessment.csv")
        validate_ta_catalog(catalog, source, source_hash)
        for record in catalog["records"]:
            opis_id = record["opis_id"]
            existing = reviews.get(opis_id)
            if existing is not None:
                if opis_id in verified_ids:
                    continue
                raise ValueError(f"TA catalog conflicts with review for {opis_id}")
            reviews[opis_id] = {
                "status": "operator_coordinate",
                "latitude": record["latitude"],
                "longitude": record["longitude"],
                "site_id": record["site_id"],
                "overture_id": None,
            }
            verified_ids.add(opis_id)
        input_hashes["ta_site_catalog"] = {
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "workbook_sha256": catalog["workbook_sha256"],
            "verified_count": catalog["verified_count"],
            "unresolved_count": catalog["unresolved_count"],
        }


def load_public(context: ReviewContext) -> None:
    root, source_hash = context.root, context.source_hash
    reviews, verified_ids, input_hashes = (
        context.reviews,
        context.verified_ids,
        context.input_hashes,
    )
    public_path = root / "data/review/de_public_sites.json"
    if public_path.exists():
        evidence_bytes = public_path.read_bytes()
        evidence = json.loads(evidence_bytes)
        output_bytes = (
            root / "var/overture_states/de_public_verified.json"
        ).read_bytes()
        output = json.loads(output_bytes)
        pdf_bytes = (root / "var/overture_states/de_truck_flyer_2024.pdf").read_bytes()
        catalog_bytes = (root / "var/overture_states/de_places.ndjson").read_bytes()
        if (
            evidence["source_sha256"] != source_hash
            or output["source_sha256"] != source_hash
            or output["evidence_sha256"] != hashlib.sha256(evidence_bytes).hexdigest()
            or output["public_source_sha256"] != hashlib.sha256(pdf_bytes).hexdigest()
            or output["overture_catalog_sha256"]
            != hashlib.sha256(catalog_bytes).hexdigest()
            or output["verified_count"] != len(output["records"])
            or len(output["records"]) != len(evidence["records"])
        ):
            raise ValueError("Delaware public-site validation is stale")
        for record, validated in zip(
            evidence["records"], output["records"], strict=True
        ):
            opis_id = record["opis_id"]
            if (
                opis_id in reviews
                or any(validated[key] != value for key, value in record.items())
                or validated["site_status"] != "verified"
                or validated["routing_status"] != "pending_check"
            ):
                raise ValueError(f"Delaware public-site link conflicts for {opis_id}")
            reviews[opis_id] = {
                "status": "site_verified",
                "site_id": validated["site_id"],
                "overture_id": record["overture_id"],
                "latitude": record["latitude"],
                "longitude": record["longitude"],
            }
            verified_ids.add(opis_id)
        input_hashes["de_public_sites"] = {
            "evidence_sha256": output["evidence_sha256"],
            "validated_output_sha256": hashlib.sha256(output_bytes).hexdigest(),
            "public_source_sha256": output["public_source_sha256"],
            "overture_catalog_sha256": output["overture_catalog_sha256"],
            "verified_count": output["verified_count"],
        }
