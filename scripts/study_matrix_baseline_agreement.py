"""Compare ORS direct matrix and directions on varied trip endpoints.

Research-only: two hosted attempts per case, outside the production request.
No credential or route geometry is written to the report.
"""

import argparse
import json
import os
from decimal import Decimal
from time import perf_counter

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from app.api.inputs import resolve_inputs  # noqa: E402
from app.api.validation import TripInput  # noqa: E402
from app.planning.budget import RequestBudget  # noqa: E402
from app.routing.client import OpenRouteServiceAdapter  # noqa: E402
from scripts.benchmark_matrix_selector import CASES  # noqa: E402
from scripts.check_pilot_routes import routing_key  # noqa: E402


def study(case: str) -> dict:
    start_spec, finish_spec, _ = CASES[case]
    request = TripInput(
        start_spec,
        finish_spec,
        Decimal(50),
        Decimal("3.20"),
        Decimal(0),
        None,
        None,
    )
    start, finish = resolve_inputs(request)
    points = start.coordinates, finish.coordinates
    budget = RequestBudget(deadline_seconds=30)
    adapter = OpenRouteServiceAdapter(routing_key())
    started = perf_counter()
    try:
        matrix = adapter.matrix(points, budget)
        directions = adapter.route(points, budget)
    finally:
        adapter.close()
    assert matrix.durations_seconds is not None
    matrix_seconds = matrix.durations_seconds[0][1]
    matrix_meters = matrix.distances_meters[0][1]
    if matrix_seconds is None or matrix_meters is None:
        return {"case": case, "status": "matrix_direct_unreachable"}
    return {
        "case": case,
        "status": "compared",
        "hosted_attempts": budget.attempts,
        "matrix_seconds": str(matrix_seconds),
        "directions_seconds": str(directions.duration_seconds),
        "time_difference_seconds": str(directions.duration_seconds - matrix_seconds),
        "matrix_meters": str(matrix_meters),
        "directions_meters": str(directions.distance_meters),
        "distance_difference_meters": str(directions.distance_meters - matrix_meters),
        "elapsed_seconds": round(perf_counter() - started, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="+", choices=CASES)
    args = parser.parse_args()
    for case in args.cases:
        try:
            print(json.dumps(study(case)))
        except Exception as error:
            print(json.dumps({"case": case, "status": type(error).__name__}))


if __name__ == "__main__":
    main()
