"""Match numbered QuikTrip CSV sites to public operator location pages."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from urllib.parse import urlparse

import httpx

from app.stations.catalogs.paths import ROOT as ROOT
from app.stations.catalogs.paths import SOURCE as SOURCE
from app.stations.catalogs.quiktrip_site_catalog import CSV_NUMBER as CSV_NUMBER
from app.stations.catalogs.quiktrip_site_catalog import PAGES as PAGES
from app.stations.catalogs.quiktrip_site_catalog import (
    source_stations as source_stations,
)
from app.stations.catalogs.quiktrip_site_catalog import (
    validate_catalog as validate_catalog,
)
from app.stations.catalogs.source_queue import build_queue

SITEMAP_URL = "https://locations.quiktrip.com/sitemap1.xml"
CATALOG = ROOT / "var/operator_review/quiktrip_site_catalog.json"
SCRIPT = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S
)
STORE_NUMBER = re.compile(r"\bQuikTrip\s*#\s*(\d+)\b", re.I)
EXTRA_URLS = (
    "https://locations.quiktrip.com/mo/bellefontaine/10845-lilac-ave",
    "https://locations.quiktrip.com/ga/doraville/4086-pleasantdale-rd",
    "https://locations.quiktrip.com/mo/st-peters/391-n-main-st",
    "https://locations.quiktrip.com/mo/pleasant-valley/8600-pleasant-valley-rd",
    "https://locations.quiktrip.com/tx/northlake/8141-gasoline-alley",
    "https://locations.quiktrip.com/ne/gretna/11105-sapp-brothers-dr",
    "https://locations.quiktrip.com/tx/windcrest/7280-ih-35-n",
    "https://locations.quiktrip.com/sc/cayce/1600-dixiana-rd",
    "https://locations.quiktrip.com/il/mt-vernon/4501-veterans-memorial-dr",
    "https://locations.quiktrip.com/ok/moore/300-nw-27th-st",
)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def parse_page(url: str, data: bytes) -> dict:
    html = data.decode("utf-8", errors="replace")
    documents = [json.loads(raw) for raw in SCRIPT.findall(html)]
    businesses = [
        node
        for document in documents
        for node in document.get("@graph", [])
        if node.get("@type") == "AutomotiveBusiness" and node.get("url") == url
    ]
    measured = [
        document["credentialSubject"]
        for document in documents
        if "credentialSubject" in document
    ]
    if len(businesses) != 1 or len(measured) != 1:
        raise ValueError("operator business or measured location missing")
    business = businesses[0]
    site = measured[0]
    match = STORE_NUMBER.search(business.get("description") or "")
    heading = re.search(r"QuikTrip\s+STORE\s*#\s*(\d+)", html, re.I)
    if match is None:
        match = heading
    address = business["address"]
    if (
        match is None
        or heading is None
        or match.group(1).lstrip("0") != heading.group(1).lstrip("0")
        or any(
            address[key] != site["address"][key]
            for key in (
                "addressCountry",
                "addressLocality",
                "addressRegion",
                "postalCode",
            )
        )
        or address["streetAddress"] != site["address"]["streetAddress"].split("\n")[0]
        or address["addressCountry"] != "US"
        or site["geo"]["@type"] != "GeoCoordinates"
    ):
        raise ValueError("operator store identity or geometry differs")
    path = urlparse(url).path.strip("/").split("/")
    if (
        len(path) != 3
        or path[0].upper() != address["addressRegion"]
        or path[1] != slug(address["addressLocality"])
    ):
        raise ValueError("operator URL and address disagree")
    return {
        "url": url,
        "page_sha256": hashlib.sha256(data).hexdigest(),
        "store_number": match.group(1).lstrip("0"),
        "state": address["addressRegion"],
        "city": address["addressLocality"],
        "address": address["streetAddress"],
        "latitude": float(site["geo"]["latitude"]),
        "longitude": float(site["geo"]["longitude"]),
        "operator_record_id": business["@id"],
    }


async def fetch_pages() -> dict:
    source = source_stations()
    queue = build_queue()
    target_ids = {item["opis_id"] for item in queue["stations"]}
    if CATALOG.exists():
        prior = json.loads(CATALOG.read_text())
        target_ids.update(item["opis_id"] for item in prior["records"])
    target_city_keys = {
        (state.lower(), slug(city))
        for opis_id in target_ids
        if opis_id in source
        for state in source[opis_id]["states"]
        for city in source[opis_id]["cities"]
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(SITEMAP_URL)
        response.raise_for_status()
        sitemap_bytes = response.content
        urls = [
            node.text
            for node in ET.fromstring(sitemap_bytes).iter()
            if node.tag.endswith("loc")
        ]
        selected = sorted(
            {
                url
                for url in urls
                if (parts := urlparse(url).path.strip("/").split("/"))
                and len(parts) == 3
                and (parts[0], parts[1]) in target_city_keys
            }
            | (set(EXTRA_URLS) & set(urls))
        )
        semaphore = asyncio.Semaphore(6)

        async def fetch_one(url: str) -> tuple[dict | None, dict | None]:
            async with semaphore:
                for attempt in range(3):
                    try:
                        page = await client.get(url)
                        page.raise_for_status()
                        return parse_page(url, page.content), None
                    except (
                        httpx.HTTPError,
                        ValueError,
                        KeyError,
                        json.JSONDecodeError,
                    ) as error:
                        if attempt == 2:
                            return None, {"url": url, "error": type(error).__name__}
                        await asyncio.sleep(0.4 * (attempt + 1))
            raise AssertionError("unreachable")

        results = await asyncio.gather(*(fetch_one(url) for url in selected))
    pages = [record for record, _ in results if record is not None]
    failures = [failure for _, failure in results if failure is not None]
    document = {
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "sitemap_url": SITEMAP_URL,
        "sitemap_sha256": hashlib.sha256(sitemap_bytes).hexdigest(),
        "selected_url_count": len(selected),
        "page_count": len(pages),
        "failure_count": len(failures),
        "pages": pages,
        "failures": failures,
    }
    temporary = PAGES.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, PAGES)
    return document


def build() -> dict:
    pages_bytes = PAGES.read_bytes()
    pages = json.loads(pages_bytes)
    queue = build_queue()
    source = source_stations()
    eligible = {item["opis_id"] for item in queue["stations"]}
    prior = json.loads(CATALOG.read_text()) if CATALOG.exists() else None
    if prior is not None:
        if prior["source_sha256"] != queue["source_sha256"]:
            raise ValueError("previous QuikTrip catalog belongs to another CSV")
        if prior["pages_sha256"] == hashlib.sha256(pages_bytes).hexdigest():
            validate_catalog(prior, pages)
        eligible.update(item["opis_id"] for item in prior["records"])
    by_number: dict[str, list[dict]] = defaultdict(list)
    for page in pages["pages"]:
        by_number[page["store_number"]].append(page)
    records = []
    unresolved = []
    for opis_id in sorted(eligible, key=int):
        item = source.get(opis_id)
        if item is None:
            continue
        matches = by_number.get(item["store_number"], [])
        if len(matches) == 1:
            page = matches[0]
            if item["states"] == [page["state"]]:
                records.append(
                    {
                        "opis_id": opis_id,
                        "site_id": f"quiktrip:{item['store_number']}",
                        "identity_conflict": (
                            None
                            if page["city"].casefold()
                            in {city.casefold() for city in item["cities"]}
                            else "city_label_differs"
                        ),
                        **page,
                    }
                )
                continue
        unresolved.append(
            {
                "opis_id": opis_id,
                "store_number": item["store_number"],
                "reason": "missing_or_conflicting_operator_page",
                "candidate_count": len(matches),
            }
        )
    if prior is not None and not {item["opis_id"] for item in prior["records"]} <= {
        item["opis_id"] for item in records
    }:
        raise ValueError("existing QuikTrip links disappeared")
    catalog = {
        "status": "complete_attempt",
        "source_sha256": queue["source_sha256"],
        "pages_sha256": hashlib.sha256(pages_bytes).hexdigest(),
        "sitemap_sha256": pages["sitemap_sha256"],
        "verified_count": len(records),
        "unresolved_count": len(unresolved),
        "records": records,
        "unresolved": unresolved,
    }
    validate_catalog(catalog, pages)
    temporary = CATALOG.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, CATALOG)
    return catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    if args.fetch:
        pages = asyncio.run(fetch_pages())
        print(pages["page_count"], "pages,", pages["failure_count"], "failures")
    result = build()
    print(
        result["verified_count"], "verified;", result["unresolved_count"], "unresolved"
    )


if __name__ == "__main__":
    main()
