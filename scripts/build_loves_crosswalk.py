"""Reconcile numbered Love's source IDs with its official station-code workbook."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.stations.catalogs.loves_crosswalk import NS as NS
from app.stations.catalogs.loves_crosswalk import integer_cell as integer_cell
from app.stations.catalogs.loves_crosswalk import read_codes as read_codes
from app.stations.catalogs.loves_crosswalk import reconcile as reconcile

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "var/operator_review/loves_queue.json"
WORKBOOK = ROOT / "var/operator_review/loves_station_codes.xlsx"
OUTPUT = ROOT / "var/operator_review/loves_crosswalk.json"
SOURCE_URL = (
    "https://edge.sitecorecloud.io/lovestravela9dd-loves2161-lovesprod042a-cf5a/"
    "media/images/bililng-station-codes/station-code-master-list-91626-excel.xlsx"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    if args.fetch:
        response = httpx.get(SOURCE_URL, timeout=30, follow_redirects=True)
        response.raise_for_status()
        WORKBOOK.parent.mkdir(parents=True, exist_ok=True)
        WORKBOOK.write_bytes(response.content)
    workbook_bytes = WORKBOOK.read_bytes()
    queue_bytes = QUEUE.read_bytes()
    queue = json.loads(queue_bytes)
    codes = read_codes(WORKBOOK)
    rows = reconcile(queue["stations"], codes)
    counts = Counter(item["status"] for item in rows)
    output = {
        "purpose": "operator OPIS/store crosswalk; no coordinates accepted",
        "source_url": SOURCE_URL,
        "workbook_sha256": hashlib.sha256(workbook_bytes).hexdigest(),
        "queue_sha256": hashlib.sha256(queue_bytes).hexdigest(),
        "source_sha256": queue["source_sha256"],
        "built_at_utc": datetime.now(UTC).isoformat(),
        "workbook_store_count": len(codes),
        "station_count": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "stations": rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Reconciled {len(rows)} Love's IDs: {dict(sorted(counts.items()))}")


if __name__ == "__main__":
    main()
