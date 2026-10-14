"""Independent fixed-itinerary LP comparisons using already verified preflight legs."""

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

from app.domain.units import METERS_PER_MILE
from scripts.nationwide_quality_metrics import percentile


def itinerary_cost(legs, prices, initial, reserve):
    consumptions = [float(Decimal(v) / METERS_PER_MILE / 10) for v in legs]
    count = len(prices)
    if len(legs) != count + 1:
        raise ValueError("independent itinerary has a different leg count")
    if not count:
        if sum(consumptions) + reserve > initial + 1e-6:
            raise ValueError("direct itinerary cannot retain reserve")
        return 0.0
    rows, limits, cumulative = [], [], 0.0
    for station in range(count):
        cumulative += consumptions[station]
        rows.append([-1.0 if i < station else 0.0 for i in range(count)])
        limits.append(initial - cumulative - reserve)
        rows.append([1.0 if i <= station else 0.0 for i in range(count)])
        limits.append(50 - initial + cumulative)
    rows.append([-1.0] * count)
    limits.append(initial - sum(consumptions) - reserve)
    solved = linprog(
        np.array(prices),
        A_ub=np.array(rows),
        b_ub=np.array(limits),
        bounds=(0, None),
        method="highs",
    )
    if not solved.success:
        raise ValueError("independent itinerary LP has no optimum")
    return float(solved.fun)


def compare(report, offers):
    comparisons, references = [], []
    observed = {row["case"]: row for row in report["rows"]}
    for name, witness in report["feasibility"].items():
        if witness["status"] != "checked_feasible":
            continue
        case = report["sample"]["cases"][name]
        cost = itinerary_cost(
            witness["leg_distances_meters"],
            [float(offers[v]) for v in witness["station_ids"]],
            float(case["initial_fuel_gallons"]),
            float(case["reserve_gallons"]),
        )
        reference = {
            "case": name,
            "station_ids": witness["station_ids"],
            "reference_cost": round(cost, 8),
        }
        references.append(reference)
        row = observed.get(name)
        if row and row["contract_ok"]:
            actual = float(row["purchase_cost"])
            gap = (actual - cost) / cost * 100 if cost > 1e-6 else None
            comparisons.append(
                {
                    **reference,
                    "online_cost": actual,
                    "gap_percent": gap,
                    "gap_dollars": actual - cost,
                }
            )
    gaps = [v["gap_percent"] for v in comparisons if v["gap_percent"] is not None]
    return {
        "method": "SciPy/HiGHS LP on independent verified preflight itineraries",
        "scope": "Alternative feasible itineraries; no nationwide optimum bound",
        "planner_sha256": report["planner_sha256"],
        "candidate_sha256": report["candidate_sha256"],
        "measurement_complete": bool(report.get("finished_utc")),
        "measured_requests": len(report["rows"]),
        "expected_requests": len(report["sample"]["cases"]),
        "reference_script_sha256": hashlib.sha256(
            Path(__file__).read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest(),
        "additional_hosted_calls": 0,
        "references": references,
        "comparisons": comparisons,
        "median_gap_percent": percentile(gaps, 50),
        "p95_gap_percent": percentile(gaps, 95),
        "positive_cost_comparisons": len(gaps),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    if (
        hashlib.sha256(args.candidates.read_bytes()).hexdigest()
        != report["candidate_sha256"]
    ):
        raise ValueError("reference prices differ from the measured candidate snapshot")
    offers = {
        row["station_id"]: row["price_per_gallon"]
        for row in json.loads(args.candidates.read_text())
    }
    result = compare(report, offers)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"references", "comparisons"}}
        )
    )


if __name__ == "__main__":
    main()
