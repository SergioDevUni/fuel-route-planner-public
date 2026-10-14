from copy import deepcopy

import pytest

from scripts.reuse_performance_references import reuse


def reports():
    previous = {
        "candidate_sha256": "candidates",
        "source_sha256": "prices",
        "road": {"graph_version": "roads"},
        "cases": {"trip": {"initial": "20"}},
        "planner_sha256": "old-code",
        "asset_sha256": "old-assets",
        "finished_utc": "2026-10-13T00:00:00+00:00",
        "references": [
            {
                "case": "trip",
                "reference_cost": "100",
                "online_cost": "110",
                "gap_percent": "10",
                "refill_cost": "120",
                "hosted_attempts": 3,
            }
        ],
    }
    report = deepcopy(previous)
    report["references"] = []
    report["rows"] = [
        {
            "case": "trip",
            "cache_state": "routing_cold",
            "trial": 1,
            "contract_ok": True,
            "profile": "ors_fastest_driving_car",
            "purchase_cost": "90",
        }
    ]
    return report, previous


def test_reuse_recomputes_gaps_and_retains_original_reference_identity():
    report, previous = reports()
    result = reuse(report, previous, "source-hash")
    row = result["references"][0]
    assert row["gap_percent"] == "-10.0"
    assert row["refill_savings_percent"] == "25.00"
    assert row["hosted_attempts"] == 0
    assert row["historical_hosted_attempts"] == 3
    assert result["reference_reuse"]["planner_sha256"] == "old-code"
    assert previous["references"][0]["gap_percent"] == "10"
    assert not report["references"]


@pytest.mark.parametrize(
    "field", ["candidate_sha256", "source_sha256", "road", "cases"]
)
def test_reuse_rejects_changed_prices_inputs_or_road_scope(field):
    report, previous = reports()
    report[field] = "changed"
    with pytest.raises(ValueError, match="reference inputs differ"):
        reuse(report, previous, "hash")


def test_reuse_rejects_changed_online_profile():
    report, previous = reports()
    report["rows"][0]["profile"] = "different"
    with pytest.raises(ValueError, match="road profile differs"):
        reuse(report, previous, "hash")
