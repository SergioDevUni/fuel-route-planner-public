from decimal import Decimal

import pytest

from scripts.performance_metrics import (
    comparison_summary,
    http_summary,
    percentage_saved,
    refill_reference,
)


def test_failure_remains_in_success_denominator_and_timing_distribution():
    summary = http_summary(
        [
            {"contract_ok": True, "seconds": 12, "hosted_attempts": 3},
            {"contract_ok": False, "seconds": 0.1, "error": "ROUTING_UNAVAILABLE"},
        ]
    )
    assert summary["success_rate_percent"] == 50
    assert summary["successful_plan_p95_seconds"] == 12
    assert summary["all_response_p95_seconds"] == 11.405
    assert summary["errors"] == {"ROUTING_UNAVAILABLE": 1}


def test_refill_policy_caps_purchases_at_remaining_need_and_retains_reserve():
    cost, ending = refill_reference(
        Decimal(10),
        Decimal(2),
        (Decimal(5), Decimal(20), Decimal(30)),
        (Decimal(4), Decimal(3)),
    )
    assert cost == Decimal("186.00")
    assert ending == 2


def test_reference_rejects_an_unreachable_first_stop():
    with pytest.raises(ValueError, match="reserve"):
        refill_reference(
            Decimal(1), Decimal(0), (Decimal(2), Decimal(3)), (Decimal(4),)
        )


def test_savings_keep_negative_values_and_handle_zero_baselines():
    assert percentage_saved("110", "100") == "-10.0"
    assert percentage_saved("0", "0") is None


def test_missing_references_and_zero_costs_have_explicit_counts():
    summary = comparison_summary(
        [
            {"status": "unavailable"},
            {
                "status": "zero_purchase_case",
                "reference_cost": "0",
                "gap_percent": None,
            },
            {
                "status": "verified_independent_mip",
                "reference_cost": "100",
                "gap_percent": "2",
                "refill_savings_percent": "-2",
            },
        ]
    )
    assert summary["cases"] == 3
    assert summary["independent_verified_references"] == 1
    assert summary["zero_purchase_cases"] == 1
    assert summary["refill_savings_comparable_cases"] == 1
    assert summary["median_savings_against_refill_policy_percent"] == -2
    assert summary["reference_failures"] == {"unavailable": 1}
