from decimal import Decimal

import pytest

from scripts.nationwide_quality_metrics import cost_gap, summarize


def test_cost_gap_handles_a_zero_minimum_without_division_or_false_success():
    assert cost_gap("0", "0")["percent"] == "0"
    positive = cost_gap("5", "0")
    assert positive["percent"] is None
    assert positive["zero_reference_with_positive_spend"]
    assert Decimal(cost_gap("102", "100")["percent"]) == 2
    with pytest.raises(ValueError):
        cost_gap("NaN", "10")


def test_failures_and_missing_references_remain_in_quality_denominator():
    rows = [
        {
            "contract_ok": True,
            "seconds": 2,
            "purchase_cost": "100",
            "cost_gap": cost_gap("100", "100"),
        },
        {
            "contract_ok": True,
            "seconds": 12,
            "purchase_cost": "105",
            "cost_gap": cost_gap("105", "100"),
        },
        {"contract_ok": True, "seconds": 3, "purchase_cost": "10"},
        {"contract_ok": False, "seconds": 15},
    ]
    result = summarize(rows)
    assert result["verified_successes"] == 3
    assert result["failures_or_contract_errors"] == 1
    assert result["verified_within_10_seconds"] == 2
    assert result["successes_without_comparable_reference"] == 1
    assert (
        result["best_known_cost_gap_thresholds"]["1"]["share_of_all_requests"] == 0.25
    )
    assert not result["working_empirical_target"]["met_on_sample"]


def test_matching_own_incumbent_does_not_meet_independent_quality_target():
    row = {
        "contract_ok": True,
        "seconds": 4,
        "purchase_cost": "100",
        "cost_gap": cost_gap("100", "100"),
    }
    assert not summarize([row])["working_empirical_target"]["met_on_sample"]
    checked = {**row, "independent_reference_available": True}
    assert summarize([checked])["working_empirical_target"]["met_on_sample"]
    assert not summarize([checked, {"contract_ok": False, "seconds": 15}])[
        "working_empirical_target"
    ]["met_on_sample"]


def test_quality_at_thirteen_seconds_meets_target_but_hard_ceiling_still_applies():
    row = {
        "contract_ok": True,
        "seconds": 13,
        "purchase_cost": "100",
        "cost_gap": cost_gap("100", "100"),
        "independent_reference_available": True,
    }
    result = summarize([row])
    assert result["verified_within_10_seconds"] == 0
    assert result["verified_within_15_seconds"] == 1
    target = result["working_empirical_target"]
    assert target["met_on_sample"]
    assert not target["preferred_timing_is_acceptance_gate"]
    assert not summarize([{**row, "seconds": 15.001}])["working_empirical_target"][
        "met_on_sample"
    ]
