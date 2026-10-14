from dataclasses import FrozenInstanceError, replace

import pytest

from app.planning.budget import RequestBudget
from app.planning.settings import PlannerSettings


def test_request_and_children_keep_one_immutable_configuration(monkeypatch):
    monkeypatch.setenv("FUEL_MATRIX_FIRST", "0")
    budget = RequestBudget()
    monkeypatch.setenv("FUEL_MATRIX_FIRST", "1")
    assert not budget.settings.matrix_first
    assert budget.child(1).settings is budget.settings
    assert RequestBudget().settings.matrix_first
    with pytest.raises(FrozenInstanceError):
        budget.settings.matrix_first = True


@pytest.mark.parametrize(
    "values",
    [
        {"FUEL_PLANNING_CAPACITY": "0"},
        {"FUEL_MATRIX_FIRST": "yes"},
        {
            "FUEL_LOCAL_OSRM_URL": "http://localhost",
            "FUEL_LOCAL_OSRM_BOUNDS": "nan,0,1,2",
        },
        {
            "FUEL_LOCAL_OSRM_URL": "http://localhost",
            "FUEL_LOCAL_OSRM_VERSION": "v1",
            "FUEL_LOCAL_OSRM_BOUNDS": "1,0,-1,2",
        },
    ],
)
def test_invalid_settings_fail_at_composition(values):
    with pytest.raises(ValueError):
        PlannerSettings.from_environment(values)


def test_cache_identity_tracks_strategy_and_excludes_credentials():
    original = PlannerSettings(ors_api_key="private")
    assert "private" not in repr(original.routing_identity())
    assert (
        original.routing_identity()
        == replace(original, ors_api_key="other").routing_identity()
    )
    for change in (
        {"matrix_first": False},
        {"valhalla_version": "new"},
        {"road_database": "other.db"},
        {"partial_portfolio": True},
    ):
        assert (
            original.routing_identity()
            != replace(original, **change).routing_identity()
        )
