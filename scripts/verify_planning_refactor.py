"""Pair preserved search/matrix owners against current code on captured roads."""

import argparse
import importlib.util
import json
import logging
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from scripts.local_common import ROOT, planner_hash, sha256


def load_original(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("Preserved owner unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def compare(baseline, road_facts):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from app.planning.budget import RequestBudget
    from app.planning.graph_search import search_fuel_graph
    from app.planning.matrix_selection import select_matrix
    from scripts.benchmarking.offline_replay import calculate_replay, load_replays

    old_search = load_original(baseline / "graph_search.py", "preserved_search")
    old_matrix = load_original(baseline / "matrix_selection.py", "preserved_matrix")
    old_incumbent = load_original(
        baseline / "graph_incumbent.py", "preserved_incumbent"
    )
    from app.planning.graph_incumbent import initialize_incumbent

    index, cases = load_replays(ROOT, road_facts)
    rows = []
    for name, case in cases.items():
        outcomes = []
        for selection, search, incumbent in (
            (
                old_matrix.select_matrix,
                old_search.search_fuel_graph,
                old_incumbent.initialize_incumbent,
            ),
            (select_matrix, search_fuel_graph, initialize_incumbent),
        ):

            def bounded(*args, _search=search, **kwargs):
                kwargs["max_expansions"] = 3000
                return _search(*args, **kwargs)

            budget = RequestBudget(clock=lambda: 0.0)
            with ExitStack() as stack:
                for owner in (
                    "app.planning.hosted_planning",
                    "app.planning.matrix_first",
                ):
                    stack.enter_context(patch(owner + ".select_matrix", selection))
                stack.enter_context(
                    patch.object(old_matrix, "search_fuel_graph", bounded)
                )
                stack.enter_context(
                    patch("app.planning.matrix_selection.search_fuel_graph", bounded)
                )
                stack.enter_context(
                    patch.object(old_search, "initialize_incumbent", incumbent)
                )
                stack.enter_context(
                    patch("app.planning.graph_search.initialize_incumbent", incumbent)
                )
                try:
                    outcome = {"payload": calculate_replay(case, index, budget)}
                except Exception as error:
                    outcome = {"error": type(error).__name__, "message": str(error)}
            outcomes.append((outcome, budget.attempts))
        rows.append(
            {
                "case": name,
                "identical_payload_or_error": outcomes[0][0] == outcomes[1][0],
                "attempts_before": outcomes[0][1],
                "attempts_after": outcomes[1][1],
            }
        )
    return {
        "planner_sha256": planner_hash(ROOT),
        "road_facts_sha256": sha256(road_facts),
        "baseline_sha256": {
            name: sha256(baseline / name)
            for name in ("graph_search.py", "matrix_selection.py", "graph_incumbent.py")
        },
        "hosted_attempts": 0,
        "rows": rows,
        "method": "Paired captured roads, fixed logical clock and 3000 expansions; "
        "no new routing calls",
        "passed": bool(rows)
        and all(
            row["identical_payload_or_error"]
            and row["attempts_before"] == row["attempts_after"]
            for row in rows
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--road-facts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a fresh output")
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        report = compare(args.baseline, args.road_facts)
    finally:
        logging.disable(previous)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "cases": len(report["rows"])}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
