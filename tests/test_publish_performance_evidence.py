from copy import deepcopy

import pytest

from scripts.publish_performance_evidence import (
    STATES,
    check_public_fields,
    repeat_observations,
    summarize_report,
)


def complete_report():
    names = [f"route_{index}" for index in range(6)]
    rows = [
        {
            "case": name,
            "cache_state": state,
            "trial": 1,
            "contract_ok": index != 0,
            "seconds": 1,
            "error": "ROUTING_UNAVAILABLE" if index == 0 else None,
            **({"trip_class": "long"} if index else {}),
        }
        for index, name in enumerate(names)
        for state in STATES
    ]
    concurrent_rows, batches = [], []
    for users in (1, 3, 6):
        for state in STATES:
            for offset in range(0, len(names), users):
                batch = names[offset : offset + users]
                concurrent_rows.extend(
                    {
                        "case": name,
                        "cache_state": state,
                        "concurrency": users,
                        "contract_ok": True,
                        "seconds": 1,
                    }
                    for name in batch
                )
                batches.append(
                    {
                        "concurrency": users,
                        "cache_state": state,
                        "cases": batch,
                        "requests": users,
                        "seconds": 2,
                    }
                )
    return {
        "finished_utc": "2026-10-12T00:00:00+00:00",
        "protocol": {"repeats": 1},
        "cases": dict.fromkeys(names, {}),
        "endpoint_states": ["CA", "NY"],
        "rows": rows,
        "concurrent_rows": concurrent_rows,
        "batches": batches,
        "references": [
            {"case": names[0], "status": "unavailable"},
            *[
                {
                    "case": name,
                    "status": "verified_independent_mip",
                    "online_valid": True,
                    "reference_cost": "100",
                    "online_cost": "110",
                    "gap_percent": "10",
                    "refill_cost": "100",
                    "refill_savings_percent": "-10",
                }
                for name in names[1:]
            ],
        ],
    }


def test_publication_keeps_failed_cases_and_negative_savings():
    report = complete_report()
    summary = summarize_report(report)
    assert (
        summary["nationwide_sample"]["first_cold_trial"]["success_rate_percent"]
        == 83.333
    )
    assert summary["class_summary"]["unclassified/routing_cold"]["requests"] == 1
    assert summary["comparison_summary"]["reference_failures"] == {"unavailable": 1}
    assert summary["comparison_by_trip_class"]["unclassified"]["cases"] == 1
    assert summary["comparison_by_trip_class"]["long"]["cases"] == 5
    assert (
        summary["comparison_summary"]["aggregate_savings_against_refill_policy_percent"]
        == -10
    )
    assert (
        summary["concurrent_summary"]["6/routing_cold"][
            "requests_per_second_in_measured_batches"
        ]
        == 3
    )


@pytest.mark.parametrize("field", ["rows", "references", "concurrent_rows", "batches"])
def test_publication_rejects_missing_and_duplicate_observations(field):
    report = complete_report()
    for duplicate in (False, True):
        changed = deepcopy(report)
        if duplicate:
            changed[field].append(deepcopy(changed[field][0]))
        else:
            changed[field].pop()
        with pytest.raises(ValueError):
            summarize_report(changed)


def test_publication_rejects_an_unfinished_run():
    report = complete_report()
    report.pop("finished_utc")
    with pytest.raises(ValueError, match="not completed"):
        summarize_report(report)


def test_publication_validates_the_declared_concurrency_scope():
    report = complete_report()
    report["protocol"]["concurrency_levels"] = [3, 6]
    for field in ("concurrent_rows", "batches"):
        report[field] = [row for row in report[field] if row["concurrency"] != 1]
    summary = summarize_report(report)
    assert "1/routing_cold" not in summary["concurrent_summary"]
    assert summary["concurrent_summary"]["6/routing_cold"]["requests"] == 6
    report["concurrent_rows"].pop()
    with pytest.raises(ValueError, match="missing or duplicated"):
        summarize_report(report)


@pytest.mark.parametrize("levels", [[], [3, 3], [0, 6]])
def test_publication_rejects_invalid_concurrency_scope(levels):
    report = complete_report()
    report["protocol"]["concurrency_levels"] = levels
    with pytest.raises(ValueError, match="invalid declared"):
        summarize_report(report)


@pytest.mark.parametrize(
    "value",
    [
        {"rows": [{"result_token": "private"}]},
        {"ORS_API_KEY": "private"},
        {"value": "Bearer private"},
        {"link": "http://localhost/map#token=private"},
    ],
)
def test_publication_rejects_nested_private_access_fields(value):
    with pytest.raises(ValueError, match="private"):
        check_public_fields(value)


def test_repeat_measurements_distinguish_cache_reuse_from_recalculation():
    result = repeat_observations(
        [
            {"contract_ok": True, "stage_seconds": {"input_resolution": 0.1}},
            {"contract_ok": True, "stage_seconds": {"plan_execution": 13}},
            {"contract_ok": False, "stage_seconds": {"plan_execution": 1}},
        ]
    )
    assert result["requests"] == 3
    assert result["successful_completed_result_reuses"] == 1
    assert result["successful_recalculations"] == 1
    assert result["unsuccessful_requests"] == 1
