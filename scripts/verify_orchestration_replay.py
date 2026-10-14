"""Compare captured-road behavior with a preserved pre-refactor hosted owner."""

import argparse
import importlib.util
import json
import logging
import os
import sys
from dataclasses import fields
from inspect import signature
from pathlib import Path
from unittest.mock import patch

from scripts.local_common import ROOT, planner_hash, sha256


def compare(baseline: Path, road_facts: Path) -> dict:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    from app.planning.budget import RequestBudget
    from app.planning.hosted_planning import plan_hosted
    from app.planning.matrix_selection import search_fuel_graph
    from app.planning.request import PlanningRequest
    from scripts.benchmarking.offline_replay import calculate_replay, load_replays

    source = baseline / "app/planning/hosted_planning.py"
    spec = importlib.util.spec_from_file_location("replay_baseline_hosted", source)
    if spec is None or spec.loader is None:
        raise ValueError("preserved hosted owner is unavailable")
    original = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = original
    spec.loader.exec_module(original)
    index, cases = load_replays(ROOT, road_facts)

    def bounded(*args, **kwargs):
        kwargs["max_expansions"] = 3000
        return search_fuel_graph(*args, **kwargs)

    def baseline_hosted(context, request, **options):
        # Historical source snapshots predate the immutable internal request.
        if "request" in signature(original.plan_hosted).parameters:
            return original.plan_hosted(context, request, **options)
        inputs = {
            field.name: getattr(request, field.name)
            for field in fields(PlanningRequest)
        }
        return original.plan_hosted(context, **inputs, **options)

    rows = []
    with patch("app.planning.matrix_selection.search_fuel_graph", bounded):
        for name, case in cases.items():
            observations = []
            for implementation in (baseline_hosted, plan_hosted):
                budget = RequestBudget(clock=lambda: 0.0)
                with patch("app.planning.trip.plan_hosted", implementation):
                    try:
                        result = {"payload": calculate_replay(case, index, budget)}
                    except Exception as error:
                        result = {"error": type(error).__name__, "message": str(error)}
                observations.append((result, budget.attempts))
            rows.append(
                {
                    "case": name,
                    "identical_payload_or_error": observations[0][0]
                    == observations[1][0],
                    "attempts_before": observations[0][1],
                    "attempts_after": observations[1][1],
                    "error": observations[1][0].get("error"),
                }
            )
    return {
        "planner_sha256": planner_hash(ROOT),
        "baseline_source_sha256": sha256(source),
        "road_facts_sha256": sha256(road_facts),
        "hosted_attempts": 0,
        "method": (
            "Paired original/refactored hosted functions; identical captured roads, "
            "policy-limit snaps and checkpoint publication; fixed logical clock and "
            "3000-expansion bound. HTTP/local/portfolio behavior is covered by tests."
        ),
        "passed": all(
            row["identical_payload_or_error"]
            and row["attempts_before"] == row["attempts_after"]
            for row in rows
        ),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--road-facts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a fresh path")
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        report = compare(args.baseline, args.road_facts)
    finally:
        logging.disable(previous_logging)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "cases": len(report["rows"])}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
