"""Validate and publish completed fixed-sample delivery measurements."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.domain.units import METERS_PER_MILE
from scripts.feasibility_evidence import classify, outcome_summary
from scripts.local_common import ROOT, planner_hash
from scripts.performance_metrics import http_summary
from scripts.publish_performance_evidence import check_public_fields

START = "<!-- nationwide-evidence:start -->"
END = "<!-- nationwide-evidence:end -->"


def check_witness_record(item, case, start, finish, by_id):
    """Revalidate a saved positive witness without using the API plan."""
    ids = item["station_ids"]
    expected = (start, *(by_id[value].coordinates for value in ids), finish)
    points = item["waypoints"]
    legs = item["leg_distances_meters"]
    if len(points) != len(expected) or len(legs) != len(expected) - 1:
        raise ValueError("independent witness shape differs from its station sequence")
    for index, (point, coordinate) in enumerate(zip(points, expected, strict=True)):
        if point["requested"] != {
            "latitude": coordinate.latitude,
            "longitude": coordinate.longitude,
        }:
            raise ValueError(
                "independent witness coordinate differs from its frozen input"
            )
        snap = Decimal(point["snap_distance_meters"])
        limit = 1000 if index in (0, len(points) - 1) else 250
        if not snap.is_finite() or not 0 <= snap <= limit:
            raise ValueError("independent witness exceeds a snap allowance")
    reserve = Decimal(case["reserve_gallons"])
    for index, raw in enumerate(legs):
        distance = Decimal(raw)
        initial = Decimal(case["initial_fuel_gallons"]) if index == 0 else Decimal(50)
        if (
            not distance.is_finite()
            or not 0 <= distance <= (initial - reserve) * 10 * METERS_PER_MILE
        ):
            raise ValueError("independent witness cannot retain its fuel reserve")
    extra = Decimal(item["extra_duration_seconds"])
    if not extra.is_finite() or extra > 1800:
        raise ValueError("independent witness exceeds its extra-driving allowance")


def checked_summary(report):
    if not report.get("finished_utc"):
        raise ValueError("nationwide run has not completed")
    names = set(report["sample"]["cases"])
    if Counter(row["case"] for row in report["rows"]) != Counter(names):
        raise ValueError("each frozen input must have one measured response")
    if set(report["feasibility"]) != names:
        raise ValueError("each frozen input must have a preflight outcome")
    measured = datetime.fromisoformat(report["measurements_started_utc"])
    if datetime.fromisoformat(report["preflight_finished_utc"]) > measured or any(
        datetime.fromisoformat(item["established_utc"]) > measured
        for item in report["feasibility"].values()
    ):
        raise ValueError("all preflight outcomes must precede measurements")
    for row in report["rows"]:
        if row["outcome"] != classify(row, report["feasibility"][row["case"]]):
            raise ValueError("recorded outcome differs from independent classification")
    return outcome_summary(report["rows"], report["feasibility"])


def markdown(report):
    summary = report["summary"]
    timing = report["timing"]
    return "\n".join(
        [
            START,
            "## Expanded US route sample",
            "",
            f"On {report['created_utc'][:10]}, {report['release']} measured "
            f"{summary['requests']} fixed inputs from all 48 contiguous states.",
            "Each state supplied six inputs across short, regional, and long "
            "geodesic distance groups, with two starting fuel levels.",
            "Two additional inputs retain Oakland to Fresno at 10 and 50 gallons.",
            "The sample gives each state equal weight. "
            "Inputs were frozen before the run.",
            "",
            "Independent feasibility checks completed "
            "before measured requests started.",
            "They used all-offer distance bounds or road itineraries with fuel, "
            "reserve, snap, and extra-driving checks.",
            "",
            "| Outcome | Requests |",
            "| --- | --- |",
            f"| Verified trip plan | {summary['completed_plans']} |",
            "| Correct infeasible rejection | "
            f"{summary['correct_infeasible_rejections']} |",
            f"| Unexpected failure | {summary['unexpected_failures']} |",
            f"| Unresolved rejection | {summary['unresolved_rejections']} |",
            "",
            f"For independently feasible inputs, "
            f"{summary['completed_independently_feasible_requests']}/"
            f"{summary['independently_feasible_requests']} requests "
            "produced verified plans "
            f"({summary['feasible_plan_completion_percent']:.2f}%).",
            f"Across all inputs, plan production was "
            f"{timing['verified_successes']}/{timing['requests']} "
            f"({timing['success_rate_percent']:.2f}%).",
            f"Successful-plan p95 timing was "
            f"{timing['successful_plan_p95_seconds']:.3f} seconds.",
            f"{timing['verified_within_15_seconds']} requests returned verified plans "
            "within 15 seconds.",
            "Each input used one routing-cold request through Waitress and "
            "live openrouteservice. Prepared assets stayed loaded.",
            "Provider pacing and independent preflight "
            "were outside the measured timer.",
            "",
            "Read the [frozen sample](tests/fixtures/nationwide-sample.json), "
            "[individual measurements]"
            "(var/private-development/records/docs/evidence/nationwide-delivery.json), "
            "and [evidence method](docs/EVIDENCE_METHOD.md).",
            "The separate performance capture below supplies repeated-request, "
            "concurrency, and independent cost comparisons.",
            END,
        ]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--update-readme", action="store_true")
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    report["summary"] = checked_summary(report)
    descriptor = json.loads((ROOT / "local-release.json").read_text(encoding="utf-8"))
    identities = {
        "planner_sha256": planner_hash(ROOT),
        "candidate_sha256": descriptor["dataset"]["candidate_sha256"],
        "asset_sha256": descriptor["sha256"],
        "sample_sha256": hashlib.sha256(
            (ROOT / "tests/fixtures/nationwide-sample.json").read_bytes()
        ).hexdigest(),
    }
    if any(report.get(key) != value for key, value in identities.items()):
        raise ValueError("nationwide evidence identity differs from the frozen release")
    if report["road"] != descriptor["road"]:
        raise ValueError("road checkpoint differs from the measured release")
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from app.api.assets import load_candidates
    from app.api.inputs import resolve_inputs
    from app.api.validation import TripInput
    from app.locations.models import GazetteerVersion
    from scripts.feasibility_evidence import first_leg_bound

    if (
        GazetteerVersion.objects.get(status="active").source_sha256
        != report["sample"]["gazetteer_sha256"]
    ):
        raise ValueError("gazetteer differs from the frozen sample")
    index = load_candidates(
        descriptor["dataset"]["source_sha256"], report["candidate_sha256"]
    )
    by_id = {station.station_id: station for station in index.stations}
    for name, case in report["sample"]["cases"].items():
        trip = TripInput(
            case["start"],
            case["finish"],
            Decimal(case["initial_fuel_gallons"]),
            Decimal("3.20"),
            Decimal(case["reserve_gallons"]),
            None,
            None,
            "balanced",
            Decimal(30),
        )
        start, finish = resolve_inputs(trip)
        item = report["feasibility"][name]
        if item["status"] == "checked_feasible":
            check_witness_record(
                item, case, start.coordinates, finish.coordinates, by_id
            )
        elif item["status"] == "checked_infeasible":
            bound = first_leg_bound(
                start.coordinates,
                finish.coordinates,
                index.stations,
                trip.initial_fuel_gallons,
                trip.reserve_gallons,
            )
            if (
                bound != {key: item[key] for key in bound}
                or bound["status"] != "checked_infeasible"
            ):
                raise ValueError("infeasibility bound differs from frozen station data")
    for name, expected in report["benchmark_source_sha256"].items():
        if (
            hashlib.sha256(
                (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            != expected
        ):
            raise ValueError(f"measurement source differs: {name}")
    for history in report.get("checkpoint_source_history", []):
        for item in history["sources"]:
            archived = (
                (ROOT / item["archived_path"]).read_bytes().replace(b"\r\n", b"\n")
            )
            if hashlib.sha256(archived).hexdigest() != item["sha256"]:
                raise ValueError("archived checkpoint source differs")
    report["timing"] = http_summary(report["rows"])
    report["summary_by_stratum"] = {
        stratum: outcome_summary(
            [
                row
                for row in report["rows"]
                if report["sample"]["cases"][row["case"]]["stratum"] == stratum
            ],
            report["feasibility"],
        )
        for stratum in ("short", "regional", "long", "regression")
    }
    report["summary_by_origin_state"] = {
        state: outcome_summary(
            [
                row
                for row in report["rows"]
                if report["sample"]["cases"][row["case"]]["start_state"] == state
            ],
            report["feasibility"],
        )
        for state in report["sample"]["states"]
    }
    check_public_fields(report)
    path = (
        ROOT / "var/private-development/records/docs/evidence/nationwide-delivery.json"
    )
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.update_readme:
        path = ROOT / "README.md"
        text = path.read_text(encoding="utf-8")
        if START in text:
            begin, end = text.index(START), text.index(END) + len(END)
            text = text[:begin] + markdown(report) + text[end:]
        else:
            anchor = (
                "<!-- performance-evidence:start -->"
                if "<!-- performance-evidence:start -->" in text
                else "## Start the API"
            )
            text = text.replace(anchor, markdown(report) + "\n\n" + anchor, 1)
        path.write_text(text, encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
