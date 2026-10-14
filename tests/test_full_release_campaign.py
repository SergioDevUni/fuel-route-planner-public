"""A complete frozen campaign passes; plausible reduced campaigns do not."""

from copy import deepcopy

import pytest

from scripts.benchmarking.campaign import (
    CACHE_STATES,
    CONCURRENT_CASES,
    HOSTED_CONCURRENCY,
    campaign_spec,
)
from scripts.local_common import ROOT
from scripts.release_acceptance import acceptance


@pytest.fixture
def evidence():
    spec = campaign_spec(ROOT, "candidate")
    identity = {
        key: spec[key]
        for key in (
            "planner_sha256",
            "asset_sha256",
            "candidate_sha256",
            "source_sha256",
            "road",
            "benchmark_source_sha256",
        )
    }
    names = list(spec["nationwide_sample"]["cases"])
    nationwide = {
        **identity,
        "sample_sha256": spec["sample_sha256"],
        "sample": deepcopy(spec["nationwide_sample"]),
        "finished_utc": "2026-10-12T03:00:00+00:00",
        "measurements_started_utc": "2026-10-12T02:00:00+00:00",
        "preflight_finished_utc": "2026-10-12T01:00:00+00:00",
        "feasibility": {
            name: {
                "status": "checked_feasible",
                "established_utc": "2026-10-12T01:00:00+00:00",
            }
            for name in names
        },
        "rows": [
            {
                "case": name,
                "http_status": 200,
                "contract_ok": True,
                "contract_errors": [],
                "outcome": "completed_plan",
            }
            for name in names
        ],
    }
    row = {
        "http_status": 200,
        "contract_ok": True,
        "seconds": 1,
        "trip_class": "regional",
        "purchase_cost": "100",
    }
    performance = {
        **identity,
        "finished_utc": "2026-10-12T03:00:00+00:00",
        "cases": deepcopy(spec["performance_cases"]),
        "endpoint_states": ["CA"],
        "protocol": {"repeats": 2, "concurrency_levels": list(HOSTED_CONCURRENCY)},
        "rows": [
            {**row, "case": name, "cache_state": state, "trial": trial}
            for name in spec["performance_cases"]
            for state in CACHE_STATES
            for trial in (1, 2)
        ],
        "references": [
            {
                "case": name,
                "status": "verified_independent_mip",
                "online_valid": True,
                "online_cost": "100",
                "reference_cost": "100",
                "gap_percent": "0",
                "discovery": {"batches": [{"status": "measured"}]},
                "candidates": [
                    {
                        "source": "independent_same_profile_mip",
                        "valid": True,
                        "cost": "100",
                    }
                ],
            }
            for name in spec["performance_cases"]
        ],
        "concurrent_rows": [
            {**row, "case": name, "cache_state": state, "concurrency": users}
            for users in HOSTED_CONCURRENCY
            for state in CACHE_STATES
            for name in CONCURRENT_CASES
        ],
        "batches": [
            {
                "concurrency": users,
                "cache_state": state,
                "cases": list(CONCURRENT_CASES[offset : offset + users]),
                "requests": users,
                "seconds": 1,
            }
            for users in HOSTED_CONCURRENCY
            for state in CACHE_STATES
            for offset in range(0, 18, users)
        ],
    }
    return {
        "nationwide": nationwide,
        "performance": performance,
        "deadline": {"passed": True, "planner_sha256": "candidate"},
        "expected_planner": "candidate",
        "specification": spec,
        "installs": {
            **identity,
            "platforms": {"Windows": {"passed": True}, "Linux": {"passed": True}},
        },
    }


def test_full_frozen_campaign_passes(evidence):
    assert acceptance(**evidence)["passed"]


@pytest.mark.parametrize(
    "key", ["asset_sha256", "candidate_sha256", "source_sha256", "road"]
)
def test_restore_evidence_must_install_the_matching_assets(evidence, key):
    evidence["installs"][key] = "another-release"
    result = acceptance(**evidence)
    assert not result["passed"] and not result["checks"]["restore_installs"]


def test_self_consistent_six_user_only_campaign_fails(evidence):
    report = evidence["performance"]
    report["protocol"]["concurrency_levels"] = [6]
    report["concurrent_rows"] = [
        r for r in report["concurrent_rows"] if r["concurrency"] == 6
    ]
    report["batches"] = [b for b in report["batches"] if b["concurrency"] == 6]
    result = acceptance(**evidence)
    assert not result["passed"]
    assert "1/3/6" in result["details"]["performance_error"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_batch",
        "duplicate_row",
        "missing_route",
        "substituted_input",
        "missing_reference",
        "hidden_reference",
        "invented_gap",
        "pending_check_itinerary",
        "malformed_cost",
        "failed_observation_gap",
        "single_trial",
        "substituted_nationwide",
    ],
)
def test_incomplete_or_substituted_campaign_fails(evidence, mutation):
    report = evidence["performance"]
    if mutation == "missing_batch":
        report["batches"].pop()
    elif mutation == "duplicate_row":
        report["rows"].append(deepcopy(report["rows"][0]))
    elif mutation == "missing_route":
        name = CONCURRENT_CASES[0]
        report["concurrent_rows"] = [
            r for r in report["concurrent_rows"] if r["case"] != name
        ]
        report["batches"] = [b for b in report["batches"] if name not in b["cases"]]
    elif mutation == "substituted_input":
        report["cases"][next(iter(report["cases"]))]["initial_fuel_gallons"] = "0"
    elif mutation == "missing_reference":
        report["references"][0]["status"] = "unavailable"
    elif mutation == "hidden_reference":
        report["references"][0]["online_valid"] = False
    elif mutation == "invented_gap":
        report["references"][0]["reference_cost"] = "50"
    elif mutation == "pending_check_itinerary":
        report["references"][0]["candidates"][0]["valid"] = False
    elif mutation == "malformed_cost":
        report["references"][0]["reference_cost"] = "not-a-cost"
    elif mutation == "failed_observation_gap":
        report["references"][0]["online_valid"] = False
        report["rows"][0].update(contract_ok=False, http_status=503)
    elif mutation == "single_trial":
        report["protocol"]["repeats"] = 1
        report["rows"] = [r for r in report["rows"] if r["trial"] == 1]
    else:
        case = next(iter(evidence["nationwide"]["sample"]["cases"].values()))
        case["initial_fuel_gallons"] = "0"
    assert not acceptance(**evidence)["passed"]


@pytest.mark.parametrize(
    "key",
    [
        "planner_sha256",
        "benchmark_source_sha256",
        "asset_sha256",
        "candidate_sha256",
        "source_sha256",
        "road",
    ],
)
def test_changed_evidence_identity_fails(evidence, key):
    evidence["performance"][key] = "different"
    result = acceptance(**evidence)
    assert not result["checks"]["campaign_identity"]
    assert not result["passed"]
