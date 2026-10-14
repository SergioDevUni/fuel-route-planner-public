"""Link assessment TA/Petro rows to the operator's downloadable site workbook."""

import argparse
import hashlib
import json
from pathlib import Path

import httpx

from app.stations.catalogs.ta_site_catalog import NS as NS
from app.stations.catalogs.ta_site_catalog import NUMBER as NUMBER
from app.stations.catalogs.ta_site_catalog import WORDS as WORDS
from app.stations.catalogs.ta_site_catalog import operator_brand as operator_brand
from app.stations.catalogs.ta_site_catalog import read_workbook as read_workbook
from app.stations.catalogs.ta_site_catalog import reconcile as reconcile
from app.stations.catalogs.ta_site_catalog import source_stations as source_stations
from app.stations.catalogs.ta_site_catalog import title_key as title_key
from app.stations.catalogs.ta_site_catalog import validate_catalog as validate_catalog

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/raw/fuel-prices-for-be-assessment.csv"
PROFILE = ROOT / "data/audit/source-profile.json"
WORKBOOK = ROOT / "var/operator_review/ta_locations.xlsx"
OUTPUT = ROOT / "var/operator_review/ta_site_catalog.json"
SOURCE_URL = "https://www.ta-petro.com/api/locationsdata/download/"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fetch", action="store_true", help="refresh official workbook"
    )
    args = parser.parse_args()
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if source_hash != json.loads(PROFILE.read_text())["sha256"]:
        raise ValueError("source CSV differs from audited input")
    if args.fetch:
        response = httpx.get(SOURCE_URL, timeout=30, follow_redirects=True)
        response.raise_for_status()
        WORKBOOK.parent.mkdir(parents=True, exist_ok=True)
        WORKBOOK.write_bytes(response.content)
    workbook = WORKBOOK.read_bytes()
    sites = read_workbook(workbook)
    source = source_stations(SOURCE)
    linked, unresolved = reconcile(source, sites)
    result = {
        "status": "complete_attempt",
        "source_sha256": source_hash,
        "workbook_sha256": hashlib.sha256(workbook).hexdigest(),
        "source_url": SOURCE_URL,
        "site_count": len(sites),
        "source_id_count": len(source),
        "verified_count": len(linked),
        "unresolved_count": len(unresolved),
        "sites": sites,
        "records": linked,
        "unresolved": unresolved,
    }
    validate_catalog(result, source, source_hash)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"linked {len(linked)}/{len(source)} TA/Petro CSV IDs")


if __name__ == "__main__":
    main()
