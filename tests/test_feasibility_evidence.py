import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.domain.routing import Coordinates, Route, RouteLeg, RouteWaypoint
from app.domain.units import METERS_PER_MILE
from scripts.feasibility_evidence import (
    classify,
    first_leg_bound,
    outcome_summary,
    station_chain,
    witness,
)


def station(name, latitude, longitude):
    return SimpleNamespace(
        station_id=name, coordinates=Coordinates(latitude, longitude)
    )


def route(miles, seconds=100, snap=0):
    points = tuple(Coordinates(0, index) for index in range(len(miles) + 1))
    return Route(
        points,
        tuple(
            RouteWaypoint(point, point, index, Decimal(snap))
            for index, point in enumerate(points)
        ),
        tuple(
            RouteLeg(Decimal(value) * METERS_PER_MILE, Decimal(seconds) / len(miles))
            for value in miles
        ),
        sum((Decimal(value) * METERS_PER_MILE for value in miles), Decimal(0)),
        Decimal(seconds),
        "fixture",
        datetime.now(UTC),
    )


def test_bound_checks_all_offers_and_allows_snaps():
    start, finish = Coordinates(0, 0), Coordinates(0, 3)
    far = station("far", 0, 2)
    result = first_leg_bound(start, finish, (far,), Decimal(10), Decimal(2))
    assert result["status"] == "checked_infeasible"
    near = station("near", 0, 0.1)
    assert (
        first_leg_bound(start, finish, (far, near), Decimal(10), Decimal(2))["status"]
        == "undetermined"
    )
    # A station slightly beyond raw inventory may remain reachable after snaps.
    nearby = station("snappable", 0, 0.009)
    assert (
        first_leg_bound(start, finish, (nearby,), Decimal("0.05"), Decimal(0))["status"]
        == "undetermined"
    )


def test_witness_checks_first_inventory_capacity_reserve_duration_and_snaps():
    baseline = route([300])
    assert (
        witness(route([80, 480]), baseline, ["s"], Decimal(10), Decimal(2))["status"]
        == "checked_feasible"
    )
    for candidate in (
        route([81, 480]),
        route([80, 481]),
        route([80, 480], seconds=1901),
        route([80, 480], snap=1001),
    ):
        assert (
            witness(candidate, baseline, ["s"], Decimal(10), Decimal(2))["status"]
            == "undetermined"
        )


def test_rejection_needs_independent_evidence_and_expected_failures_stay_failures():
    rejected = {
        "contract_ok": False,
        "http_status": 422,
        "error": "NO_FEASIBLE_PLAN_IN_SEARCH_SCOPE",
    }
    assert (
        classify(rejected, {"status": "checked_infeasible"})
        == "correct_infeasible_rejection"
    )
    assert classify(rejected, {"status": "checked_feasible"}) == "unexpected_failure"
    assert classify(rejected, {}) == "unresolved_rejection"
    assert (
        classify(
            {"contract_ok": False, "http_status": 503}, {"status": "checked_infeasible"}
        )
        == "unexpected_failure"
    )
    assert (
        classify({"contract_ok": True}, {"status": "checked_infeasible"})
        == "unexpected_failure"
    )


def test_feasible_completion_denominator_includes_rejected_feasible_requests():
    rows = [
        {"case": "a", "contract_ok": True},
        {"case": "b", "contract_ok": False},
        {
            "case": "c",
            "contract_ok": False,
            "http_status": 422,
            "error": "NO_FEASIBLE_PLAN_IN_SEARCH_SCOPE",
        },
    ]
    evidence = {
        "a": {"status": "checked_feasible"},
        "b": {"status": "checked_feasible"},
        "c": {"status": "checked_infeasible"},
    }
    summary = outcome_summary(rows, evidence)
    assert summary["feasible_plan_completion_percent"] == 50
    assert summary["correct_infeasible_rejections"] == 1
    assert summary["unexpected_failures"] == 1


def test_witness_selector_is_not_itself_feasibility_evidence():
    assert station_chain(route([300]), (), Decimal(10), Decimal(2)) == ()
    assert (
        first_leg_bound(
            Coordinates(0, 0),
            Coordinates(0, 3),
            (station("near", 0, 0.1),),
            Decimal(10),
            Decimal(2),
        )["status"]
        == "undetermined"
    )


def test_frozen_sample_covers_every_contiguous_state_with_fixed_strata():
    sample = json.loads(
        (Path(__file__).parents[1] / "tests/fixtures/nationwide-sample.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(sample["states"]) == 48
    assert len(sample["cases"]) == 290
    ordinary = [
        case for case in sample["cases"].values() if case["stratum"] != "regression"
    ]
    assert set(case["start_state"] for case in ordinary) == set(sample["states"])
    assert Counter(case["start_state"] for case in ordinary) == Counter(
        dict.fromkeys(sample["states"], 6)
    )
    assert Counter(case["stratum"] for case in ordinary) == {
        "short": 96,
        "regional": 96,
        "long": 96,
    }
    assert sample["cases"]["oakland_fresno_10"]["initial_fuel_gallons"] == "10"
    assert sample["cases"]["oakland_fresno_50"]["initial_fuel_gallons"] == "50"


def test_preflight_pacing_does_not_consume_route_deadline(monkeypatch):
    from app.planning.budget import RequestBudget
    from scripts.nationwide_preflight import preflight_case

    now = [0.0]
    monkeypatch.setattr(
        "app.planning.budget.RequestBudget",
        lambda **kwargs: RequestBudget(clock=lambda: now[0], **kwargs),
    )

    def wait(_):
        now[0] += 61

    def fetch(points, budget):
        budget.check_deadline()
        assert budget.remaining_seconds == 45
        budget.consume_attempt()
        return route([10])

    pacing = SimpleNamespace(before_request=wait, record=lambda _: None)
    result = preflight_case(
        {"initial_fuel_gallons": "50", "reserve_gallons": "0"},
        Coordinates(0, 0),
        Coordinates(0, 0.01),
        (),
        SimpleNamespace(route=fetch),
        {},
        pacing,
    )
    assert result["status"] == "checked_feasible"
    assert result["hosted_attempts"] == 1
