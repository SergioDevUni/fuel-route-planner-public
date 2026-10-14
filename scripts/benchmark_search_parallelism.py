"""Compare indexed search threads/processes on arbitrary saved graphs, no I/O.

Worker startup, submission and transfer are inside each absolute job budget.
This component benchmark does not establish verified end-to-end API quality.
"""

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from decimal import Decimal
from multiprocessing import get_context
from pathlib import Path
from time import monotonic, perf_counter

from app.domain.fuel import FuelOpportunity
from app.planning.fuel_graph import DirectedFuelGraph
from app.planning.graph_search import search_fuel_graph


def job(arguments: tuple) -> dict:
    name, graph, initial, reserve, limit, deadline = arguments
    began = perf_counter()
    result = search_fuel_graph(
        graph,
        initial,
        reserve,
        max_total_seconds=limit,
        stop_when=lambda: monotonic() >= deadline,
    )
    return {
        "case": name,
        "worker_seconds": round(perf_counter() - began, 4),
        "complete": result.complete,
        "expansions": result.expansions,
        "labels_pruned": result.labels_pruned,
        "graph_cost": str(result.solution.purchase_cost) if result.solution else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--cases", nargs="+", required=True)
    parser.add_argument("--seconds", type=float, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("positive search budget required")
    source = json.loads(args.source.read_text(encoding="utf-8"))
    inputs = []
    for name in args.cases:
        raw, case = source["captured_hosted_graphs"][name], source["cases"][name]

        def matrix(field, values=raw):
            return tuple(
                tuple(None if v is None else Decimal(v) for v in row)
                for row in values[field]
            )

        graph = DirectedFuelGraph(
            tuple(
                FuelOpportunity(v["station_id"], Decimal(v["price_per_gallon"]))
                for v in raw["stations"]
            ),
            matrix("distances_meters"),
            raw["version"],
            matrix("durations_seconds"),
        )
        inputs.append(
            (
                name,
                graph,
                Decimal(case["initial"]),
                Decimal(case["reserve"]),
                graph.durations_seconds[0][-1] + Decimal(1800),
            )
        )
    report = {
        "source": str(args.source),
        "scope": "saved-graph component only",
        "job_deadline_seconds": args.seconds,
        "groups": [],
    }

    def batch(executor, mode):
        began = perf_counter()
        deadline = monotonic() + args.seconds
        futures = [executor.submit(job, (*one, deadline)) for one in inputs]
        rows = [future.result(timeout=args.seconds + 5) for future in futures]
        group = {
            "mode": mode,
            "batch_seconds": round(perf_counter() - began, 4),
            "rows": rows,
        }
        report["groups"].append(group)
        print(json.dumps(group), flush=True)

    with ThreadPoolExecutor(max_workers=len(inputs)) as executor:
        batch(executor, "threads")
    with ProcessPoolExecutor(
        max_workers=len(inputs), mp_context=get_context("spawn")
    ) as executor:
        batch(executor, "processes_cold")
        batch(executor, "processes_warm")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
