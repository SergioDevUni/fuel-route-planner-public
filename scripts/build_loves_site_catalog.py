"""Prepare Love's site coordinates from its public sitemap and exact CSV crosswalk.

This is an offline, paced build. Failures remain unresolved; the CSV supplies
the station identity and price. No page is requested during a trip.
"""

import argparse
import hashlib
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx

from app.stations.catalogs.paths import ROOT
from scripts.verify_loves_operator import visible_opis_codes
from scripts.verify_operator_coordinates import matching_store

SITEMAP_URL = "https://www.loves.com/sitemap-locations.xml"
OUTPUT = ROOT / "var/operator_review/loves_site_catalog.json"
STORE_SUFFIX = re.compile(r"-(\d+)$")


def sitemap_stores(xml: bytes) -> dict[int, str]:
    stores: dict[int, str] = {}
    for node in ET.fromstring(xml).iter():
        if not node.tag.endswith("loc") or not node.text:
            continue
        url = node.text.strip()
        match = STORE_SUFFIX.search(url)
        if not url.startswith("https://www.loves.com/locations/") or not match:
            continue
        number = int(match.group(1))
        if number in stores:
            raise ValueError(f"duplicate operator store #{number} in sitemap")
        stores[number] = url
    if not stores:
        raise ValueError("Love's location sitemap contains no stores")
    return stores


def verify_page(row: dict, url: str, body: bytes) -> dict:
    number = row["store_numbers"][0]
    html = body.decode("utf-8")
    store = matching_store(html, str(number))
    address = store["address"]
    geo = store["geo"]
    page_codes = visible_opis_codes(html)
    latitude = geo["latitude"]
    longitude = geo["longitude"]
    if (
        address["addressRegion"] != row["source_state"]
        or address["addressCountry"] != "US"
        or (page_codes != {int(row["opis_id"])} and len(page_codes) > 1)
        or (
            page_codes
            and page_codes != {int(row["opis_id"])}
            and address.get("addressLocality", "").casefold()
            != row["source_city"].casefold()
        )
        or not isinstance(latitude, (int, float))
        or isinstance(latitude, bool)
        or not isinstance(longitude, (int, float))
        or isinstance(longitude, bool)
        or not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not 24 <= latitude <= 50
        or not -125 <= longitude <= -66
        or not address.get("streetAddress")
    ):
        raise ValueError("operator OPIS, state, address, or coordinate mismatch")
    return {
        "opis_id": row["opis_id"],
        "store_number": number,
        "evidence_url": url,
        "page_sha256": hashlib.sha256(body).hexdigest(),
        "expected_address": address["streetAddress"],
        "operator_city": address.get("addressLocality", ""),
        "operator_state": address["addressRegion"],
        "latitude": latitude,
        "longitude": longitude,
        "coordinate_source": "operator_jsonld",
        "page_opis_status": (
            "absent"
            if not page_codes
            else "matched"
            if page_codes == {int(row["opis_id"])}
            else "conflict_csv_branch_authoritative"
        ),
        "page_opis_ids": sorted(page_codes),
        "site_status": "verified",
        "routing_status": "pending_check",
    }


def fetch_page(
    client: httpx.Client, row: dict, url: str
) -> tuple[dict | None, dict | None]:
    try:
        response = client.get(url)
        response.raise_for_status()
        return verify_page(row, url, response.content), None
    except (httpx.HTTPError, UnicodeError, KeyError, TypeError, ValueError) as error:
        return None, {"opis_id": row["opis_id"], "reason": type(error).__name__}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, help="pilot only; never publish pilot data"
    )
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--retry-unresolved", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.delay < 0.5:
        parser.error("--delay must be at least 0.5 seconds")
    if args.retry_unresolved and args.limit is not None:
        parser.error("--retry-unresolved cannot be combined with --limit")
    crosswalk_bytes = (ROOT / "var/operator_review/loves_crosswalk.json").read_bytes()
    crosswalk = json.loads(crosswalk_bytes)
    queue = (ROOT / "var/operator_review/loves_queue.json").read_bytes()
    workbook = (ROOT / "var/operator_review/loves_station_codes.xlsx").read_bytes()
    source = (ROOT / "data/raw/fuel-prices-for-be-assessment.csv").read_bytes()
    if (
        crosswalk["queue_sha256"] != hashlib.sha256(queue).hexdigest()
        or crosswalk["workbook_sha256"] != hashlib.sha256(workbook).hexdigest()
        or crosswalk["source_sha256"] != hashlib.sha256(source).hexdigest()
    ):
        raise ValueError("Love's crosswalk or source has changed")
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        sitemap_response = client.get(SITEMAP_URL)
        sitemap_response.raise_for_status()
        sitemap = sitemap_stores(sitemap_response.content)
        exact = sorted(
            (
                row
                for row in crosswalk["stations"]
                if row["status"] == "exact_operator_id"
            ),
            key=lambda row: row["store_numbers"][0],
        )
        total_exact = len(exact)
        records = []
        if args.retry_unresolved:
            prior = json.loads(OUTPUT.read_text())
            if (
                prior["status"] != "complete_attempt"
                or prior["source_sha256"] != crosswalk["source_sha256"]
                or prior["crosswalk_sha256"]
                != hashlib.sha256(crosswalk_bytes).hexdigest()
                or prior["sitemap_sha256"]
                != hashlib.sha256(sitemap_response.content).hexdigest()
                or prior["attempted_count"] != total_exact
                or prior["verified_count"] + prior["unresolved_count"] != total_exact
            ):
                raise ValueError("previous Love's catalog is stale or incomplete")
            retry_ids = {item["opis_id"] for item in prior["unresolved"]}
            if len(retry_ids) != len(prior["unresolved"]):
                raise ValueError("duplicate unresolved catalog ID")
            records = prior["records"]
            exact = [row for row in exact if row["opis_id"] in retry_ids]
        if args.limit is not None:
            exact = exact[: args.limit]
        failures = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = []
            for row in exact:
                number = row["store_numbers"][0]
                url = sitemap.get(number)
                if url is None:
                    failures.append(
                        {"opis_id": row["opis_id"], "reason": "missing_sitemap_store"}
                    )
                    continue
                time.sleep(args.delay)
                futures.append(executor.submit(fetch_page, client, row, url))
            for index, future in enumerate(as_completed(futures), 1):
                record, failure = future.result()
                if record is not None:
                    records.append(record)
                if failure is not None:
                    failures.append(failure)
                if index % 25 == 0:
                    print(
                        f"Checked {index}/{len(exact)}; "
                        f"verified {len(records)}; unresolved {len(failures)}",
                        flush=True,
                    )
    records.sort(key=lambda record: record["store_number"])
    failures.sort(key=lambda failure: int(failure["opis_id"]))
    output = {
        "schema_version": 1,
        "status": "pilot" if args.limit is not None else "complete_attempt",
        "source_sha256": crosswalk["source_sha256"],
        "crosswalk_sha256": hashlib.sha256(crosswalk_bytes).hexdigest(),
        "sitemap_url": SITEMAP_URL,
        "sitemap_sha256": hashlib.sha256(sitemap_response.content).hexdigest(),
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "exact_id_count": sum(
            row["status"] == "exact_operator_id" for row in crosswalk["stations"]
        ),
        "attempted_count": len(exact) if args.limit is not None else total_exact,
        "verified_count": len(records),
        "unresolved_count": len(failures),
        "records": records,
        "unresolved": failures,
    }
    path = OUTPUT if args.limit is None else OUTPUT.with_name("loves_site_pilot.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}: {len(records)} verified; {len(failures)} unresolved")


if __name__ == "__main__":
    main()
