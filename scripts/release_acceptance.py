"""Check the owner's historical development campaign against its chosen gates.

Despite the historical command name, these optional diagnostic gates are not
assessment requirements, release obligations or product service guarantees.
"""

import argparse
import json
from pathlib import Path

from scripts.benchmarking.campaign import (
    campaign_spec,
    check_identity,
    check_performance_protocol,
    check_reference_coverage,
)
from scripts.local_common import ROOT, planner_hash
from scripts.publish_nationwide_evidence import checked_summary
from scripts.publish_performance_evidence import summarize_report


def acceptance(
    nationwide,
    performance,
    deadline,
    *,
    expected_planner=None,
    installs=None,
    specification=None,
):
    nationwide = nationwide or {}
    performance = performance or {}
    checks = dict.fromkeys(
        (
            "nationwide_completion",
            "nationwide_sample_complete",
            "nationwide_contracts",
            "performance_sample_complete",
            "cost_quality",
            "reference_coverage",
            "performance_contracts",
        ),
        False,
    )
    checks["deadline"] = bool(deadline and deadline.get("passed") is True)
    identity = expected_planner or nationwide.get("planner_sha256")
    spec = specification or campaign_spec(ROOT, identity)
    checks["code_identity"] = bool(identity) and all(
        report and report.get("planner_sha256") == identity
        for report in (nationwide, performance, deadline)
    )
    checks["restore_installs"] = bool(
        installs
        and installs.get("planner_sha256") == identity
        and all(
            installs.get(key) == spec[key]
            for key in ("asset_sha256", "candidate_sha256", "source_sha256", "road")
        )
        and all(
            installs.get("platforms", {}).get(platform, {}).get("passed") is True
            for platform in ("Windows", "Linux")
        )
    )
    details = {}
    checks["campaign_identity"] = False
    try:
        check_identity(nationwide, spec, nationwide=True)
        check_identity(performance, spec)
        checks["campaign_identity"] = True
    except (ValueError, KeyError, TypeError) as error:
        details["identity_error"] = str(error)
    try:
        nationwide_summary = checked_summary(nationwide)
        completion = nationwide_summary["feasible_plan_completion_percent"]
        checks["nationwide_completion"] = completion is not None and completion >= 99
        checks["nationwide_sample_complete"] = (
            nationwide["sample"] == spec["nationwide_sample"]
            and len(nationwide["sample"]["cases"]) == 290
        )
        checks["nationwide_contracts"] = all(
            not row.get("contract_errors")
            for row in nationwide["rows"]
            if row.get("http_status") == 200
        )
        details["nationwide"] = nationwide_summary
    except (ValueError, KeyError, TypeError) as error:
        checks["nationwide_completion"] = False
        details["nationwide_error"] = str(error)
    try:
        summaries = summarize_report(performance)
        check_performance_protocol(performance, spec)
        check_reference_coverage(performance)
        checks["performance_sample_complete"] = (
            len(performance["cases"]) == 54 and performance["protocol"]["repeats"] == 2
        )
        quality = summaries["comparison_summary"]
        median = quality["median_gap_to_mip_reference_percent"]
        p95 = quality["p95_gap_to_mip_reference_percent"]
        checks["cost_quality"] = (
            median is not None and p95 is not None and median <= 1 and p95 <= 5
        )
        checks["reference_coverage"] = True
        checks["performance_contracts"] = all(
            row["contract_ok"] is True
            for row in performance["rows"] + performance["concurrent_rows"]
            if row.get("http_status") == 200
        )
        details["quality"] = quality
    except (ValueError, KeyError, TypeError, ArithmeticError, AttributeError) as error:
        checks["cost_quality"] = False
        details["performance_error"] = str(error)
    return {"passed": all(checks.values()), "checks": checks, "details": details}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path("var/private-development/records/docs/evidence"),
    )
    args = parser.parse_args()

    def read(name):
        path = args.evidence / name
        return json.loads(path.read_text()) if path.exists() else None

    result = acceptance(
        read("nationwide-delivery.json"),
        read("performance.json"),
        read("response-deadline.json"),
        expected_planner=planner_hash(ROOT),
        installs=read("restore-installs.json"),
    )
    args.evidence.mkdir(parents=True, exist_ok=True)
    (args.evidence / "acceptance.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
