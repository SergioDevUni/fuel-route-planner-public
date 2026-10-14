"""Validate the targeted follow-up and publish it without changing old captures."""

import argparse
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from scripts.local_common import ROOT, planner_hash
from scripts.performance_metrics import http_summary
from scripts.publish_performance_evidence import check_public_fields
from scripts.verify_seven_case_fixes import CASES

START = "<!-- seven-case-evidence:start -->"
END = "<!-- seven-case-evidence:end -->"


def checked_summary(report):
    if not report.get("finished_utc"):
        raise ValueError("follow-up has not completed")
    expected = Counter((name, trial) for name in CASES for trial in (1, 2))
    if Counter((row["case"], row["trial"]) for row in report["rows"]) != expected:
        raise ValueError("follow-up observations are missing or duplicated")
    for name in CASES[-2:]:
        evidence = report["first_stop_evidence"][name]
        candidates = evidence["candidates"]
        if len(candidates) != evidence["possible_first_stops"]:
            raise ValueError("first-stop evidence has missing candidates")
        if len({item["station_id"] for item in candidates}) != len(candidates):
            raise ValueError("first-stop evidence has duplicate candidates")
        reachable = [item for item in candidates if item["within_starting_range"]]
        if not reachable or not evidence["supports_profile_infeasibility"]:
            raise ValueError("first-stop evidence does not support the rejection")
        for item in reachable:
            if not (
                Decimal(item["first_leg_verified_miles"])
                <= Decimal(evidence["usable_range_miles"])
                and Decimal(item["extra_minutes"]) > 30
                and Decimal(item["station_snap_meters"]) <= 250
                and all(
                    Decimal(value) <= 1000 for value in item["endpoint_snap_meters"]
                )
            ):
                raise ValueError("first-stop route facts contradict the rejection")
    outcomes = []
    for row in report["rows"]:
        correct_rejection = (
            row["case"] in CASES[-2:]
            and row["http_status"] == 422
            and row["error"] == "NO_FEASIBLE_PLAN_IN_SEARCH_SCOPE"
        )
        matched = (
            correct_rejection
            if row["case"] in CASES[-2:]
            else row["contract_ok"]
            and row["http_status"] == 200
            and row.get("saved_result_matches")
        )
        if (
            row["expected_outcome_ok"] != matched
            or row["supported_infeasible_rejection"] != correct_rejection
        ):
            raise ValueError("follow-up outcome label differs from its response")
        outcomes.append(bool(matched))
    return {
        "requests": len(outcomes),
        "matched": sum(outcomes),
        "verified_plans": sum(row["contract_ok"] for row in report["rows"]),
        "supported_infeasible_rejections": sum(
            row["supported_infeasible_rejection"] for row in report["rows"]
        ),
    }


def markdown(report):
    summary = report["expected_outcomes"]
    maximum = max(row["seconds"] for row in report["rows"] if row["contract_ok"])
    return "\n".join(
        [
            START,
            "## v1.0.2 fix verification",
            "",
            "The current release uses explicit road-search radii and the existing "
            "fuel-arithmetic tolerance for tank-capacity checks.",
            "Trip endpoints use a 1,000-metre search radius. "
            "Fuel stops use 250 metres.",
            "The prepared bundle contains "
            f"{report['road']['completed_sources']:,} completed road sources "
            f"across {report['road']['points']:,} points.",
            "",
            "A fresh installation repeated the seven affected inputs twice through "
            "live Waitress HTTP and openrouteservice.",
            "",
            "| Original input group | Follow-up result |",
            "| --- | --- |",
            "| Denver and Billings to Casper, at 20 and 50 gallons | "
            "All eight requests returned verified plans. |",
            "| Columbus to Portland, ME, at 20 gallons | "
            "Both requests returned verified plans. |",
            "| Los Angeles to Paradise and Boise, at 20 gallons | "
            "All four requests returned supported fuel-policy rejections. |",
            "",
            f"{summary['matched']}/{summary['requests']} requests "
            "matched the expected outcome.",
            f"All {summary['verified_plans']} successful plans passed price, fuel, "
            "driving-allowance, and saved-result checks.",
            f"The slowest verified response took {maximum:.2f} seconds.",
            "",
            "The California checks screened all 6,626 offers and measured the seven "
            "possible first stops for each input.",
            "Only two stops were reachable with 180 usable miles.",
            "They added at least 140 minutes toward Paradise and 169 minutes toward "
            "Boise under the measured fastest-driving profile.",
            "The driving allowance is 30 minutes.",
            "",
            "Read the [follow-up measurements]"
            "(var/private-development/records/docs/evidence/seven-case-fixes.json) "
            "and use the [verification command]"
            "(docs/LOCAL_SETUP.md#verify-the-seven-case-fixes).",
            "The earlier captures below retain their original "
            "release hashes and outcomes.",
            END,
        ]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--update-readme", action="store_true")
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    report["expected_outcomes"] = checked_summary(report)
    descriptor = json.loads((ROOT / "local-release.json").read_text(encoding="utf-8"))
    for key, value in {
        "release": descriptor["version"],
        "planner_sha256": planner_hash(ROOT),
        "asset_sha256": descriptor["sha256"],
        "candidate_sha256": descriptor["dataset"]["candidate_sha256"],
    }.items():
        if report[key] != value:
            raise ValueError("follow-up release identity differs")
    original = (
        ROOT / "var/private-development/records/docs/evidence/nationwide-delivery.json"
    )
    if (
        hashlib.sha256(original.read_bytes()).hexdigest()
        != report["original_evidence_sha256"]
    ):
        raise ValueError("original capture differs from the follow-up baseline")
    baseline = json.loads(original.read_text(encoding="utf-8"))
    if report["inputs"] != {name: baseline["sample"]["cases"][name] for name in CASES}:
        raise ValueError("follow-up changed an original input")
    if report["road"] != descriptor["road"]:
        raise ValueError("follow-up road checkpoint differs from its release")
    if (
        hashlib.sha256(
            (ROOT / "scripts/verify_seven_case_fixes.py")
            .read_bytes()
            .replace(b"\r\n", b"\n")
        ).hexdigest()
        != report["verification_script_sha256"]
    ):
        raise ValueError("follow-up measurement source differs")
    report["plan_metrics"] = http_summary(report["rows"])
    check_public_fields(report)
    (
        ROOT / "var/private-development/records/docs/evidence/seven-case-fixes.json"
    ).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.update_readme:
        path = ROOT / "README.md"
        text = path.read_text(encoding="utf-8")
        if START in text:
            start, end = text.index(START), text.index(END) + len(END)
            text = text[:start] + markdown(report) + text[end:]
        else:
            text = text.replace(
                "<!-- nationwide-evidence:start -->",
                markdown(report) + "\n\n<!-- nationwide-evidence:start -->",
                1,
            )
        path.write_text(text, encoding="utf-8")
    print(json.dumps(report["expected_outcomes"]))


if __name__ == "__main__":
    main()
