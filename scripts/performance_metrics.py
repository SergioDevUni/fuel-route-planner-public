"""Descriptive HTTP metrics and an independent reference fuel policy."""

from collections import Counter
from decimal import Decimal

from app.domain.ledger import usd_cents
from scripts.nationwide_quality_metrics import percentile


def http_summary(rows: list[dict]) -> dict:
    successful = [row for row in rows if row.get("contract_ok")]
    return {
        "requests": len(rows),
        "verified_successes": len(successful),
        "success_rate_percent": round(100 * len(successful) / len(rows), 3)
        if rows
        else None,
        "errors": dict(
            Counter(
                row.get("error") or "contract_validation"
                for row in rows
                if not row.get("contract_ok")
            )
        ),
        "all_response_p50_seconds": percentile([row["seconds"] for row in rows], 50),
        "all_response_p95_seconds": percentile([row["seconds"] for row in rows], 95),
        "successful_plan_p95_seconds": percentile(
            [row["seconds"] for row in successful], 95
        ),
        "verified_within_15_seconds": sum(row["seconds"] <= 15 for row in successful),
        "hosted_attempts": sum(row.get("hosted_attempts", 0) for row in rows),
    }


def refill_reference(
    initial: Decimal,
    reserve: Decimal,
    leg_gallons: tuple[Decimal, ...],
    prices: tuple[Decimal, ...],
) -> tuple[Decimal, Decimal]:
    """Fill at each reference stop, capped by the remaining trip's fuel need."""
    if len(leg_gallons) != len(prices) + 1:
        raise ValueError("one leg is required between each reference event")
    if not 0 <= reserve <= initial <= 50:
        raise ValueError("initial inventory must fit the tank and reserve")
    if any(not value.is_finite() or value < 0 for value in (*leg_gallons, *prices)):
        raise ValueError("reference values must be finite and nonnegative")
    fuel, cost = initial, Decimal(0)
    remaining = sum(leg_gallons, Decimal(0))
    for index, consumption in enumerate(leg_gallons):
        fuel -= consumption
        remaining -= consumption
        if fuel < reserve - Decimal("0.000001"):
            raise ValueError("reference itinerary cannot retain its reserve")
        if index < len(prices):
            target = min(Decimal(50), remaining + reserve)
            purchase = max(Decimal(0), target - fuel)
            fuel += purchase
            cost += purchase * prices[index]
    return usd_cents(cost), fuel


def percentage_saved(actual: str, baseline: str) -> str | None:
    cost, reference = Decimal(actual), Decimal(baseline)
    return str((reference - cost) / reference * 100) if reference else None


def comparison_summary(rows: list[dict]) -> dict:
    comparable = [row for row in rows if row.get("reference_cost") is not None]
    gaps = [
        float(row["gap_percent"])
        for row in comparable
        if row.get("gap_percent") is not None
    ]
    savings = [
        float(row["refill_savings_percent"])
        for row in comparable
        if row.get("refill_savings_percent") is not None
    ]
    return {
        "cases": len(rows),
        "independent_verified_references": sum(
            row["status"] == "verified_independent_mip" for row in comparable
        ),
        "zero_purchase_cases": sum(
            row["status"] == "zero_purchase_case" for row in comparable
        ),
        "median_gap_to_mip_reference_percent": percentile(gaps, 50),
        "p95_gap_to_mip_reference_percent": percentile(gaps, 95),
        "median_savings_against_refill_policy_percent": percentile(savings, 50),
        "refill_savings_comparable_cases": len(savings),
        "within_one_percent_of_mip_reference": sum(value <= 1 for value in gaps),
        "reference_failures": dict(
            Counter(row["status"] for row in rows if row.get("reference_cost") is None)
        ),
    }
