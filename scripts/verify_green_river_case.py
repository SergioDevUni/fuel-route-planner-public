"""Verify two physical Love's sites without assigning assessment prices."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.stations.catalogs.green_river import verify_links as verify_links
from app.stations.catalogs.green_river import verify_source_rows as verify_source_rows
from scripts.verify_loves_operator import visible_opis_codes
from scripts.verify_operator_coordinates import matching_store

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/review/green_river_sites.json"
SOURCE = ROOT / "data/raw/fuel-prices-for-be-assessment.csv"
AUDIT = ROOT / "data/audit/source-profile.json"
WORKBOOK = ROOT / "var/operator_review/loves_station_codes.xlsx"
OUTPUT = ROOT / "var/operator_review/green_river_verified.json"


def verify_sites(evidence: dict, pages: dict[str, str]) -> list[dict]:
    sites = evidence["sites"]
    if {site["store_number"] for site in sites} != {"119", "792"}:
        raise ValueError("Green River store set differs")
    if len({site["site_id"] for site in sites}) != 2:
        raise ValueError("Green River physical-site IDs are not unique")
    if (
        len({site["phone"] for site in sites}) != 2
        or len({(site["latitude"], site["longitude"]) for site in sites}) != 2
    ):
        raise ValueError("Green River sites need distinct phones and points")
    verified = []
    for site in sites:
        html = pages[site["store_number"]]
        store = matching_store(html, site["store_number"])
        address = store["address"]
        geo = store["geo"]
        if (
            address["streetAddress"] != site["operator_address"]
            or address["addressLocality"] != site["city"]
            or address["addressRegion"] != site["state"]
            or address["addressCountry"] != "US"
            or store["telephone"] != site["phone"]
            or (geo["latitude"], geo["longitude"])
            != (site["latitude"], site["longitude"])
            or visible_opis_codes(html) != set(site["operator_opis_ids"])
            or site["access_status"] != "pending_check"
        ):
            raise ValueError(f"Love's store {site['store_number']} page differs")
        verified.append(
            {
                "site_id": site["site_id"],
                "store_number": site["store_number"],
                "latitude": site["latitude"],
                "longitude": site["longitude"],
                "operator_opis_ids": site["operator_opis_ids"],
                "access_status": "pending_check",
                "page_sha256": hashlib.sha256(html.encode()).hexdigest(),
            }
        )
    return verified


def main() -> None:
    evidence_bytes = EVIDENCE.read_bytes()
    evidence = json.loads(evidence_bytes)
    source_sha256 = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if source_sha256 != json.loads(AUDIT.read_text(encoding="utf-8"))["sha256"]:
        raise ValueError("source CSV differs from audit")
    verify_source_rows(evidence, SOURCE)
    verify_links(evidence, WORKBOOK)
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        pages = {}
        for site in evidence["sites"]:
            if not site["operator_url"].startswith(
                "https://www.loves.com/locations/ut/green-river/"
            ):
                raise ValueError("unexpected Green River operator URL")
            response = client.get(site["operator_url"])
            response.raise_for_status()
            pages[site["store_number"]] = response.text
    verified = verify_sites(evidence, pages)
    output = {
        "source_sha256": source_sha256,
        "review_evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "workbook_sha256": hashlib.sha256(WORKBOOK.read_bytes()).hexdigest(),
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "physical_site_count": len(verified),
        "price_link_status": "csv_store_matched",
        "route_ready_count": 0,
        "sites": verified,
        "source_links": evidence["source_links"],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(
        "Verified two distinct Green River sites and CSV store links; access unchecked"
    )


if __name__ == "__main__":
    main()
