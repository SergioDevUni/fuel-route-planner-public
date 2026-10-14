"""Local distance alternatives pass the same fuel/time/snap verification."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain.routing import RouteLeg
from app.planning.budget import RequestBudget
from app.planning.trip import TripPlanner
from tests.support.planning import FINISH, START, FakeCandidates, FakeRouter


@pytest.mark.parametrize("extra_seconds,accepted", [(300, True), (3600, False)])
def test_shorter_local_geometry_is_adopted_only_inside_time_policy(
    extra_seconds, accepted
):
    class Local(FakeRouter):
        supports_shorter_routes = True

        def route(self, points, budget):
            self.latest = super().route(points, budget)
            return self.latest

        def shorter_route(self, points, budget):
            assert tuple(v.requested for v in self.latest.waypoints) == points
            legs = tuple(
                RouteLeg(
                    leg.distance_meters * Decimal(".9"),
                    leg.duration_seconds
                    + Decimal(extra_seconds) / len(self.latest.legs),
                )
                for leg in self.latest.legs
            )
            return replace(
                self.latest,
                legs=legs,
                distance_meters=sum((leg.distance_meters for leg in legs), Decimal(0)),
                duration_seconds=sum(
                    (leg.duration_seconds for leg in legs), Decimal(0)
                ),
                engine_version="same-road:shorter",
            )

    local = Local()
    planner = TripPlanner(
        local,
        FakeCandidates(),
        national_refiner=SimpleNamespace(propose=lambda *args, **kwargs: None),
        verification_profile="local",
    )
    result = planner.plan_local_first(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal(3),
        budget=RequestBudget(),
    )
    assert (result.selection_method == "local_road_shorter_verified") is accepted
    assert result.verified.duration_seconds <= result.baseline.duration_seconds + 1800
    assert (
        sum((leg.distance_meters for leg in result.verified.legs), Decimal(0))
        == result.verified.distance_meters
    )
    if accepted:
        assert result.verified.engine_version == "same-road:shorter"
    else:
        assert result.verified.engine_version != "same-road:shorter"
