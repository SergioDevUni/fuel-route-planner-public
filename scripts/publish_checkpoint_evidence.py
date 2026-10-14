"""Write checkpoint observations and campaign progress to a local report."""

import argparse
import json
from pathlib import Path

from scripts.feasibility_evidence import outcome_summary
from scripts.performance_metrics import http_summary
from scripts.publish_performance_evidence import check_public_fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    if report.get("finished_utc"):
        raise ValueError("use the completed-evidence publisher for a finished run")
    expected = set(report["sample"]["cases"])
    observed = [row["case"] for row in report["rows"]]
    if len(observed) != len(set(observed)) or not set(observed) <= expected:
        raise ValueError("checkpoint has duplicate or unknown observations")
    report["publication_status"] = "incomplete_requested_call_allocation"
    report["expected_requests"] = len(expected)
    report["observed_requests"] = len(observed)
    report["pending_requests"] = sorted(expected - set(observed))
    report["summary"] = outcome_summary(report["rows"], report["feasibility"])
    report["timing"] = http_summary(report["rows"])
    check_public_fields(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": report["publication_status"],
                "observed": len(observed),
                "expected": len(expected),
                "summary": report["summary"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
