"""Public numeric rendering and the final endpoint-snap policy boundary."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.api.serialization import _string, serialize_trip
from app.api.validation import TripInput
from app.domain.location import ResolvedLocation
from app.planning.trip import TripPlanner
from app.planning.trip_types import RouteCheckFailed
from tests.support.planning import FINISH, START, FakeCandidates, FakeRouter


@pytest.mark.parametrize("value", ["-0", "-1e-18", "-0.000000004"])
def test_numerically_zero_values_do_not_leak_negative_zero(value):
    assert _string(Decimal(value)) == "0.00000000"


@pytest.mark.parametrize("endpoint", [0, -1])
@pytest.mark.parametrize("snap", ["1000", "1000.00000001"])
def test_final_serializer_endpoint_snap_policy_at_exact_boundary(endpoint, snap):
    trip = TripInput({}, {}, Decimal(50), Decimal("3.20"), Decimal(0), None, None)
    result = TripPlanner(FakeRouter(), FakeCandidates()).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        initial_price=Decimal("3.20"),
    )
    points = list(result.verified.waypoints)
    points[endpoint] = replace(points[endpoint], snap_distance_meters=Decimal(snap))
    result = replace(result, verified=replace(result.verified, waypoints=tuple(points)))
    start = ResolvedLocation(START, "coordinates", "IL")
    finish = ResolvedLocation(FINISH, "coordinates", "PA")
    if Decimal(snap) > 1000:
        with pytest.raises(RouteCheckFailed):
            serialize_trip(trip, start, finish, result)
    else:
        payload = serialize_trip(trip, start, finish, result)
        assert payload["status"] == "verified"
        assert payload["assumptions"]["station_selection"] == "fuel_cost"
        assert "optimization_evidence" not in payload["assumptions"]
