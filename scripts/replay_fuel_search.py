"""Replay captured directed matrices without provider I/O or road-cache changes.

This measures bounded graph search, not end-to-end timing or national quality.
Input cases, offers and roads come from the report; no route-specific rules.
"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.fuel import FuelOpportunity  # noqa: E402
from app.planning.fuel_graph import DirectedFuelGraph  # noqa: E402
from app.planning.graph_search import search_fuel_graph  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=3)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    report = {
        "source": str(args.source),
        "candidate_version": source["candidate_version"],
        "scope": (
            "captured bounded graphs; graph legs only; no final directions verification"
        ),
        "seconds_per_graph": args.seconds,
        "rows": [],
    }
    for name, raw in source["captured_hosted_graphs"].items():

        def matrix(key: str, values=raw) -> tuple[tuple[Decimal | None, ...], ...]:
            return tuple(
                tuple(None if v is None else Decimal(v) for v in row)
                for row in values[key]
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
        case = source["cases"][name]
        assert graph.durations_seconds is not None
        direct = graph.durations_seconds[0][-1]
        assert direct is not None
        began = perf_counter()
        result = search_fuel_graph(
            graph,
            Decimal(case["initial"]),
            Decimal(case["reserve"]),
            max_total_seconds=direct + Decimal(1800),
            max_expansions=100_000,
            stop_when=lambda started=began: perf_counter() - started >= args.seconds,
        )
        row = {
            "case": name,
            "seconds": round(perf_counter() - began, 4),
            "expansions": result.expansions,
            "labels_pruned": result.labels_pruned,
            "complete": result.complete,
            "graph_purchase_cost": str(result.solution.purchase_cost)
            if result.solution
            else None,
            "station_ids": result.solution.station_ids if result.solution else None,
        }
        report["rows"].append(row)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
