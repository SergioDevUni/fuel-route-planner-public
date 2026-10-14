"""Build an evidence-backed Pilot-family site catalog for exact CSV branches.

The operator sitemap and pages are read offline from the trip API. A matching
branch, brand, region, and locality is required; unlinked IDs stay unresolved.
"""

import argparse
import hashlib
import html
import json
import math
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from app.stations.catalogs.pilot_site_catalog import BRANCH as BRANCH
from app.stations.catalogs.pilot_site_catalog import FAMILY as FAMILY
from app.stations.catalogs.pilot_site_catalog import brand as brand
from app.stations.catalogs.pilot_site_catalog import family_branch as family_branch
from app.stations.catalogs.pilot_site_catalog import reconcile as reconcile
from app.stations.catalogs.pilot_site_catalog import source_branches as source_branches
from app.stations.catalogs.pilot_site_catalog import (
    validate_catalog as validate_catalog,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/raw/fuel-prices-for-be-assessment.csv"
PROFILE = ROOT / "data/audit/source-profile.json"
OUTPUT = ROOT / "var/operator_review/pilot_site_catalog.json"
SITEMAP_URL = "https://locations.pilotflyingj.com/sitemap.xml"
HEADING = re.compile(r'<h1\b[^>]*itemprop="name"[^>]*>(.*?)</h1>', re.I | re.S)
META = re.compile(r'<meta\b[^>]*itemprop="([^"]+)"[^>]*content="([^"]*)"', re.I)
REGION = re.compile(r'itemprop="addressRegion"[^>]*>([^<]+)<', re.I)
COUNTRY = re.compile(r'<address\b[^>]*data-country="([^"]+)"', re.I)


class IrrelevantSite(ValueError):
    """A sitemap location page belongs to another operator brand."""


def detail_urls(xml: bytes) -> list[str]:
    urls = []
    for node in ET.fromstring(xml).iter():
        if not node.tag.endswith("loc") or not node.text:
            continue
        url = node.text.strip()
        parsed = urlparse(url)
        parts = parsed.path.strip("/").split("/")
        if (
            parsed.scheme == "https"
            and parsed.netloc == "locations.pilotflyingj.com"
            and len(parts) == 4
            and parts[0] in {"us", "ca"}
        ):
            urls.append(url)
    if not urls or len(urls) != len(set(urls)):
        raise ValueError("Pilot sitemap has no unique site-page URLs")
    return sorted(urls)


def parse_page(url: str, body: bytes) -> dict:
    page = body.decode("utf-8")
    heading = HEADING.search(page)
    region = REGION.search(page)
    country = COUNTRY.search(page)
    values: dict[str, set[str]] = defaultdict(set)
    for key, value in META.findall(page):
        values[key].add(html.unescape(value))
    required = {"latitude", "longitude", "streetAddress", "addressLocality"}
    if any(len(values[key]) != 1 for key in required):
        raise ValueError("operator page has missing or conflicting site metadata")
    metadata = {key: next(iter(entries)) for key, entries in values.items() if entries}
    if heading is None or region is None or country is None:
        raise ValueError("operator page lacks site identity or address")
    name = html.unescape(re.sub(r"<[^>]+>", " ", heading.group(1))).strip()
    if brand(name) is None:
        raise IrrelevantSite("site is outside Pilot, Flying J, or One9 family")
    numbers = {int(match.group(1)) for match in BRANCH.finditer(name)}
    latitude = float(metadata["latitude"])
    longitude = float(metadata["longitude"])
    parts = urlparse(url).path.strip("/").split("/")
    country_code = country.group(1).upper()
    region_code = html.unescape(region.group(1)).strip().upper()
    if (
        len(numbers) != 1
        or brand(name) is None
        or country_code != parts[0].upper()
        or region_code != parts[1].upper()
        or not metadata.get("streetAddress")
        or not metadata.get("addressLocality")
        or not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        raise ValueError("operator page identity, locality, or coordinate invalid")
    store_number = next(iter(numbers))
    operator_brand = brand(name)
    url_digest = hashlib.sha256(url.encode()).hexdigest()[:12]
    site_id = (
        f"pilot-family:{country_code.lower()}:{region_code.lower()}:"
        f"{store_number}:{operator_brand}:{url_digest}"
    )
    return {
        "site_id": site_id,
        "store_number": store_number,
        "brand": operator_brand,
        "name": name,
        "city": metadata["addressLocality"],
        "state": region_code,
        "country": country_code,
        "address": metadata["streetAddress"],
        "latitude": latitude,
        "longitude": longitude,
        "evidence_url": url,
        "page_sha256": hashlib.sha256(body).hexdigest(),
    }


def fetch_page(client: httpx.Client, url: str) -> tuple[dict | None, dict | None]:
    try:
        response = client.get(url)
        response.raise_for_status()
        return parse_page(url, response.content), None
    except IrrelevantSite:
        return None, None
    except (httpx.HTTPError, UnicodeError, KeyError, ValueError) as error:
        return None, {"url": url, "reason": type(error).__name__}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, help="bounded pilot; never a complete catalog"
    )
    args = parser.parse_args()
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    profile = json.loads(PROFILE.read_text())
    if source_hash != profile["sha256"]:
        raise ValueError("source CSV differs from audited input")
    source = source_branches(SOURCE)
    with httpx.Client(
        timeout=25, follow_redirects=True, limits=httpx.Limits(max_connections=2)
    ) as client:
        response = client.get(SITEMAP_URL)
        response.raise_for_status()
        sitemap = response.content
        urls = detail_urls(sitemap)
        if args.limit is not None:
            if args.limit <= 0:
                raise ValueError("limit must be positive")
            urls = urls[: args.limit]
        pages = []
        failures = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {}
            for url in urls:
                futures[pool.submit(fetch_page, client, url)] = url
                time.sleep(0.5)  # At most two page starts per second.
            for index, future in enumerate(as_completed(futures), start=1):
                page, failure = future.result()
                if page is not None:
                    pages.append(page)
                if failure is not None:
                    failures.append(failure)
                if index % 100 == 0:
                    print(f"processed {index}/{len(urls)} pages", flush=True)
    linked, unresolved = reconcile(source, pages)
    output = {
        "status": "complete_attempt" if args.limit is None else "pilot_only",
        "source_sha256": source_hash,
        "sitemap_sha256": hashlib.sha256(sitemap).hexdigest(),
        "sitemap_url": SITEMAP_URL,
        "site_page_count": len(urls),
        "parsed_page_count": len(pages),
        "pages": sorted(pages, key=lambda item: item["evidence_url"]),
        "page_failures": sorted(failures, key=lambda item: item["url"]),
        "source_id_count": len(source),
        "verified_count": len(linked),
        "unresolved_count": len(unresolved),
        "records": linked,
        "unresolved": unresolved,
    }
    output_path = (
        OUTPUT
        if args.limit is None
        else OUTPUT.with_name("pilot_site_catalog_pilot.json")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"linked {len(linked)}/{len(source)} CSV IDs")
    print(f"page failures: {len(failures)}")


if __name__ == "__main__":
    main()
