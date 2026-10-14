"""Prioritize source Love's IDs for exact operator-page review; accept none."""

import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from app.stations.catalogs.regions import STATES

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/raw/fuel-prices-for-be-assessment.csv"
AUDIT = ROOT / "data/audit/source-profile.json"
EVIDENCE = ROOT / "data/review/loves_operator_evidence.json"
OUTPUT = ROOT / "var/operator_review/loves_queue.json"
BRANCH = re.compile(r"\bLOVE'?S\b[^#]*#\s*(\d+)", re.IGNORECASE)


def build_queue(rows: list[dict[str, str]], reviewed: dict[str, dict]) -> list[dict]:
    by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row["State"].strip().upper() in STATES:
            by_id[row["OPIS Truckstop ID"].strip()].append(row)
    queue = []
    for opis_id, source_rows in by_id.items():
        branches = sorted(
            {
                int(match.group(1))
                for row in source_rows
                if (match := BRANCH.search(row["Truckstop Name"]))
            }
        )
        if not branches:
            continue
        evidence = reviewed.get(opis_id)
        if evidence and len(branches) != 1:
            raise ValueError(f"reviewed Love's branch differs from source: {opis_id}")
        if (
            evidence
            and evidence.get("store_number")
            and branches[0] != int(evidence["store_number"])
        ):
            raise ValueError(f"reviewed Love's branch differs from source: {opis_id}")
        queue.append(
            {
                "opis_id": opis_id,
                "store_numbers": branches,
                "source_names": list(
                    dict.fromkeys(row["Truckstop Name"].strip() for row in source_rows)
                ),
                "source_addresses": list(
                    dict.fromkeys(row["Address"].strip() for row in source_rows)
                ),
                "city": source_rows[0]["City"].strip(),
                "state": source_rows[0]["State"].strip().upper(),
                "review_status": (
                    "site_identified_access_pending_check"
                    if evidence
                    else "ambiguous_source_branch"
                    if len(branches) > 1
                    else "pending_operator_review"
                ),
                "evidence_url": evidence["evidence_url"] if evidence else None,
            }
        )
    return sorted(queue, key=lambda item: int(item["opis_id"]))


def main() -> None:
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if source_hash != json.loads(AUDIT.read_text(encoding="utf-8"))["sha256"]:
        raise ValueError("source CSV differs from audited input")
    evidence_bytes = EVIDENCE.read_bytes()
    evidence = json.loads(evidence_bytes)
    reviewed = {item["opis_id"]: item for item in evidence["records"]}
    if len(reviewed) != len(evidence["records"]):
        raise ValueError("duplicate Love's review OPIS ID")
    review_hashes = {"loves_operator": hashlib.sha256(evidence_bytes).hexdigest()}
    for region in ("chicago", "imperial_ca"):
        path = ROOT / f"data/review/{region}_site_evidence.json"
        data = path.read_bytes()
        review_hashes[region] = hashlib.sha256(data).hexdigest()
        for item in json.loads(data)["records"]:
            if (
                item["status"] in {"site_verified", "operator_coordinate"}
                and "loves.com/locations/" in item["evidence_url"]
            ):
                if item["opis_id"] in reviewed:
                    raise ValueError("duplicate reviewed Love's OPIS ID")
                reviewed[item["opis_id"]] = item
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        queue = build_queue(list(csv.DictReader(stream)), reviewed)
    statuses = sorted({item["review_status"] for item in queue})
    counts = {
        status: sum(item["review_status"] == status for item in queue)
        for status in statuses
    }
    result = {
        "purpose": "operator identity review queue, not accepted coordinates",
        "source_sha256": source_hash,
        "review_evidence_sha256": review_hashes,
        "station_count": len(queue),
        "status_counts": counts,
        "stations": queue,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(queue)} numbered Love's IDs: {counts}")


if __name__ == "__main__":
    main()
