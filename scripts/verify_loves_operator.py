"""Verify a bounded Love's review set against CSV IDs and live operator pages."""

import argparse
import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx

from scripts.verify_operator_coordinates import matching_store

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/raw/fuel-prices-for-be-assessment.csv"
AUDIT = ROOT / "data/audit/source-profile.json"
EVIDENCE = ROOT / "data/review/loves_operator_evidence.json"
OUTPUT = ROOT / "var/operator_review/loves_verified.json"


class VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def visible_opis_codes(html: str) -> set[int]:
    parser = VisibleText()
    parser.feed(html)
    text = " ".join(parser.parts)
    return {int(code) for code in re.findall(r"\bOPIS\s*:\s*(\d+)\b", text)}


def source_rows() -> tuple[str, dict[str, list[dict[str, str]]]]:
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if source_hash != json.loads(AUDIT.read_text(encoding="utf-8"))["sha256"]:
        raise ValueError("source CSV differs from audited input")
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            grouped[row["OPIS Truckstop ID"].strip()].append(row)
    return source_hash, grouped


def verify_record(record: dict, rows: list[dict[str, str]], html: str) -> dict:
    if not rows:
        raise ValueError(f"OPIS {record['opis_id']} absent from source CSV")
    branch = int(record["store_number"])
    if not all(
        re.search(r"\bLOVE'?S\b", row["Truckstop Name"], re.IGNORECASE)
        and (match := re.search(r"#\s*(\d+)", row["Truckstop Name"]))
        and int(match.group(1)) == branch
        and row["City"].strip().casefold() == record["city"].casefold()
        and row["State"].strip().upper() == record["state"]
        for row in rows
    ):
        raise ValueError(f"OPIS {record['opis_id']} source branch or locality differs")
    codes = visible_opis_codes(html)
    if codes != {int(record["opis_id"])}:
        raise ValueError(f"operator OPIS codes differ for {record['opis_id']}: {codes}")
    store = matching_store(html, record["store_number"])
    address = store["address"]
    geo = store["geo"]
    latitude = geo["latitude"]
    longitude = geo["longitude"]
    if (
        address["streetAddress"] != record["expected_address"]
        or address["addressLocality"].casefold() != record["city"].casefold()
        or address["addressRegion"] != record["state"]
        or address["addressCountry"] != "US"
        or type(latitude) not in (int, float)
        or type(longitude) not in (int, float)
        or not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or (latitude, longitude) != (record["latitude"], record["longitude"])
    ):
        raise ValueError(
            f"operator address or coordinate differs for {record['opis_id']}"
        )
    return {
        "opis_id": record["opis_id"],
        "store_number": record["store_number"],
        "evidence_url": record["evidence_url"],
        "expected_address": record["expected_address"],
        "latitude": latitude,
        "longitude": longitude,
        "coordinate_source": "operator_jsonld",
        "site_status": "verified",
        "routing_status": "pending_check",
        "page_sha256": hashlib.sha256(html.encode()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    evidence_bytes = EVIDENCE.read_bytes()
    evidence = json.loads(evidence_bytes)
    source_hash, grouped = source_rows()
    records = evidence["records"]
    if len({item["opis_id"] for item in records}) != len(records):
        raise ValueError("duplicate Love's review OPIS ID")
    verified = []
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        for record in records:
            if record["status"] != "operator_coordinate" or not record[
                "evidence_url"
            ].startswith("https://www.loves.com/locations/"):
                raise ValueError("invalid Love's review source")
            response = client.get(record["evidence_url"])
            response.raise_for_status()
            verified.append(
                verify_record(record, grouped[record["opis_id"]], response.text)
            )
    output = {
        "source_sha256": source_hash,
        "review_evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "routing_verified_count": 0,
        "records": verified,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(verified)} Love's OPIS/store/coordinate records; no access")


if __name__ == "__main__":
    main()
