"""Compare new API observations with previously road-verified reference plans."""

import argparse
import hashlib
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from scripts.performance_metrics import percentage_saved


def reuse(report: dict, previous: dict, source_hash: str) -> dict:
    for field in ("candidate_sha256", "source_sha256", "road", "cases"):
        if report[field] != previous[field]:
            raise ValueError(f"reference inputs differ: {field}")
    if not report.get("finished_utc") or report["references"]:
        raise ValueError("a completed capture without references is required")
    if {row["case"] for row in previous["references"]} != set(report["cases"]):
        raise ValueError("reference coverage differs")
    result = deepcopy(report)
    for old in previous["references"]:
        online = next(
            row
            for row in report["rows"]
            if row["case"] == old["case"]
            and row["cache_state"] == "routing_cold"
            and row["trial"] == 1
        )
        if online["contract_ok"] and online["profile"] != "ors_fastest_driving_car":
            raise ValueError("reference road profile differs")
        row = deepcopy(old)
        row.update(
            online_valid=online["contract_ok"],
            online_cost=online.get("purchase_cost"),
            historical_hosted_attempts=old.get("hosted_attempts", 0),
            hosted_attempts=0,
            reference_reused=True,
        )
        for field in ("gap_dollars", "gap_percent", "refill_savings_percent"):
            row.pop(field, None)
        if row["online_valid"] and row.get("reference_cost") is not None:
            gap = Decimal(row["online_cost"]) - Decimal(row["reference_cost"])
            row["gap_dollars"] = str(gap)
            row["gap_percent"] = (
                str(gap / Decimal(row["reference_cost"]) * 100)
                if Decimal(row["reference_cost"])
                else None
            )
            if row.get("refill_cost") is not None:
                row["refill_savings_percent"] = percentage_saved(
                    row["online_cost"], row["refill_cost"]
                )
        result["references"].append(row)
    result["reference_reuse"] = {
        "source_sha256": source_hash,
        "planner_sha256": previous["planner_sha256"],
        "asset_sha256": previous["asset_sha256"],
        "source_finished_utc": previous["finished_utc"],
        "method": "Previously verified same-profile itineraries and frozen prices; "
        "cost gaps recomputed against new first routing-cold API observations. "
        "No new reference solver or road-verification calls.",
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("references", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output already exists")
    report = reuse(
        json.loads(args.capture.read_text(encoding="utf-8")),
        json.loads(args.references.read_text(encoding="utf-8")),
        hashlib.sha256(args.references.read_bytes()).hexdigest(),
    )
    report["reference_reuse"]["source"] = args.references.name
    report["http_capture_sha256"] = hashlib.sha256(
        args.capture.read_bytes()
    ).hexdigest()
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
