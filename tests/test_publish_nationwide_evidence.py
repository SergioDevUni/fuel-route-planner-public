from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.domain.routing import Coordinates
from scripts.publish_nationwide_evidence import check_witness_record, checked_summary


def complete_report():
    return {
        "finished_utc": "2026-10-12T21:00:00+00:00",
        "preflight_finished_utc": "2026-10-12T20:00:00+00:00",
        "measurements_started_utc": "2026-10-12T20:00:01+00:00",
        "sample": {"cases": {"a": {}, "b": {}}},
        "feasibility": {
            "a": {
                "status": "checked_feasible",
                "established_utc": "2026-10-12T19:00:00+00:00",
            },
            "b": {
                "status": "checked_infeasible",
                "established_utc": "2026-10-12T19:00:00+00:00",
            },
        },
        "rows": [
            {"case": "a", "contract_ok": True, "outcome": "completed_plan"},
            {
                "case": "b",
                "contract_ok": False,
                "http_status": 422,
                "error": "NO_FEASIBLE_PLAN_IN_SEARCH_SCOPE",
                "outcome": "correct_infeasible_rejection",
            },
        ],
    }


def test_recomputed_outcomes_keep_plan_and_rejection_distinct():
    result = checked_summary(complete_report())
    assert result["requests"] == 2
    assert result["completed_plans"] == 1
    assert result["correct_infeasible_rejections"] == 1
    assert result["feasible_plan_completion_percent"] == 100


@pytest.mark.parametrize(
    "change", ["duplicate", "missing", "late_preflight", "wrong_outcome", "unfinished"]
)
def test_publication_rejects_incomplete_or_contaminated_measurements(change):
    report = deepcopy(complete_report())
    if change == "duplicate":
        report["rows"].append(report["rows"][0])
    elif change == "missing":
        report["feasibility"].pop("b")
    elif change == "late_preflight":
        report["feasibility"]["b"]["established_utc"] = "2026-10-12T20:00:02+00:00"
    elif change == "wrong_outcome":
        report["rows"][0]["outcome"] = "correct_infeasible_rejection"
    else:
        report.pop("finished_utc")
    with pytest.raises(ValueError):
        checked_summary(report)


@pytest.mark.parametrize("change", ["coordinate", "fuel", "snap", "duration"])
def test_saved_witness_is_revalidated_against_input_and_inventory(change):
    start, finish = Coordinates(0, 0), Coordinates(0, 2)
    midway = SimpleNamespace(coordinates=Coordinates(0, 1))
    case = {"initial_fuel_gallons": "10", "reserve_gallons": "2"}
    item = {
        "station_ids": ["s"],
        "waypoints": [
            {
                "requested": {"latitude": 0, "longitude": longitude},
                "snap_distance_meters": "0",
            }
            for longitude in (0, 1, 2)
        ],
        "leg_distances_meters": ["100000", "200000"],
        "extra_duration_seconds": "100",
    }
    check_witness_record(item, case, start, finish, {"s": midway})
    if change == "coordinate":
        item["waypoints"][1]["requested"]["longitude"] = 1.1
    elif change == "fuel":
        item["leg_distances_meters"][0] = "200000"
    elif change == "snap":
        item["waypoints"][1]["snap_distance_meters"] = "251"
    else:
        item["extra_duration_seconds"] = "1801"
    with pytest.raises(ValueError):
        check_witness_record(item, case, start, finish, {"s": midway})
