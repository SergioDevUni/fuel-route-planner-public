"""Keep observed cost gaps separate from measured cost_search and failures."""

from decimal import Decimal


def cost_gap(cost: str, reference: str) -> dict:
    actual, best = Decimal(cost), Decimal(reference)
    if not actual.is_finite() or not best.is_finite() or min(actual, best) < 0:
        raise ValueError("costs must be finite and nonnegative")
    difference = actual - best
    return {
        "dollars": str(difference),
        "percent": str(difference / best * 100)
        if best
        else ("0" if not actual else None),
        "zero_reference_with_positive_spend": not best and actual > 0,
    }


def percentile(values: list[float], percent: float) -> float | None:
    """Linear sample percentile; descriptive only for a small live sample."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3
    )


def summarize(rows: list[dict]) -> dict:
    successful = [row for row in rows if row.get("contract_ok")]
    comparable = [row for row in successful if row.get("cost_gap") is not None]
    thresholds = {}
    for threshold in ("0.5", "1", "3", "5"):
        within = sum(
            row["cost_gap"]["percent"] is not None
            and Decimal(row["cost_gap"]["percent"]) <= Decimal(threshold)
            for row in comparable
        )
        thresholds[threshold] = {
            "within_count": within,
            "comparable_success_count": len(comparable),
            "all_request_count": len(rows),
            "share_of_all_requests": round(within / len(rows), 4) if rows else None,
        }
    independently_checked_within_one = sum(
        row["cost_gap"]["percent"] is not None
        and Decimal(row["cost_gap"]["percent"]) <= 1
        and (
            row.get("independent_reference_available", False)
            or Decimal(row["purchase_cost"]) == 0
        )
        for row in comparable
    )
    working_target_met = bool(rows) and (
        independently_checked_within_one / len(rows) >= 0.99
        and len(successful) / len(rows) >= 0.99
        and all(row["seconds"] <= 15 for row in successful)
    )
    return {
        "requests": len(rows),
        "verified_successes": len(successful),
        "failures_or_contract_errors": len(rows) - len(successful),
        "verified_within_10_seconds": sum(row["seconds"] <= 10 for row in successful),
        "verified_within_15_seconds": sum(row["seconds"] <= 15 for row in successful),
        "all_response_p50_seconds": percentile([row["seconds"] for row in rows], 50),
        "all_response_p95_seconds": percentile([row["seconds"] for row in rows], 95),
        "all_response_p99_seconds": percentile([row["seconds"] for row in rows], 99),
        "comparable_successes": len(comparable),
        "successes_without_comparable_reference": len(successful) - len(comparable),
        "independent_candidate_compared": sum(
            row.get("independent_reference_available", False) for row in comparable
        ),
        "zero_purchase_cases": sum(
            Decimal(row["purchase_cost"]) == 0 for row in successful
        ),
        "best_known_cost_gap_thresholds": thresholds,
        "working_empirical_target": {
            "within_one_percent_with_independent_comparison": (
                independently_checked_within_one
            ),
            "required_quality_share": 0.99,
            "preferred_response_seconds": 10,
            "preferred_timing_is_acceptance_gate": False,
            "hard_planning_ceiling_seconds": 15,
            "required_verified_success_share": 0.99,
            "met_on_sample": working_target_met,
            "scope": "cost comparisons with measured reference itineraries",
        },
    }
