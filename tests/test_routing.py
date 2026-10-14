import copy
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.domain.routing import Coordinates
from app.planning.budget import (
    PlanningDeadlineExceeded,
    RequestBudget,
    RoutingBudgetExceeded,
)
from app.routing.client import (
    DIRECTIONS_URL,
    MATRIX_URL,
    NoDrivableRoute,
    OpenRouteServiceAdapter,
    RoutingAuthenticationFailed,
    RoutingLimitExceeded,
    RoutingRequestRejected,
    RoutingUnavailable,
)
from app.routing.normalizer import RoutingResponseInvalid, normalize_ors_geojson

FIXTURE = Path(__file__).parent / "fixtures/ors_chicago_three_waypoints.json"
POINTS = (
    Coordinates(41.8781, -87.6298),
    Coordinates(41.8810, -87.6278),
    Coordinates(41.8840, -87.6205),
)


def payload() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("error", ["Quota exceeded", {"message": "Quota exceeded"}])
def test_gateway_quota_403_is_unavailability_not_authentication_failure(error):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, json={"error": error})
        )
    )
    adapter = OpenRouteServiceAdapter("test-key", client)
    try:
        with pytest.raises(RoutingUnavailable) as caught:
            adapter.route(POINTS, RequestBudget())
        assert not isinstance(caught.value, RoutingAuthenticationFailed)
    finally:
        client.close()


def test_real_three_waypoint_response_normalizes() -> None:
    route = normalize_ors_geojson(payload(), POINTS)
    assert route.distance_meters == Decimal("1450.2")
    assert [leg.distance_meters for leg in route.legs] == [
        Decimal("492.5"),
        Decimal("957.7"),
    ]
    assert len(route.geometry) == 36
    assert len(route.waypoints) == 3
    assert all(waypoint.snap_distance_meters < 10 for waypoint in route.waypoints)


def test_directions_search_matches_endpoint_and_station_snap_allowances() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["coordinates"] == [
            list(point.as_provider_pair()) for point in POINTS
        ]
        assert body["radiuses"] == [1000, 250, 1000]
        return httpx.Response(200, json=payload())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        route = OpenRouteServiceAdapter("test-key", client).route(
            POINTS, RequestBudget()
        )
    assert route.distance_meters == Decimal("1450.2")


@pytest.mark.parametrize("mutation", ["missing_segments", "bad_leg", "bad_order"])
def test_malformed_provider_response_rejected(mutation: str) -> None:
    result = copy.deepcopy(payload())
    properties = result["features"][0]["properties"]
    if mutation == "missing_segments":
        properties.pop("segments")
    elif mutation == "bad_leg":
        properties["segments"][0]["distance"] = 99999
    else:
        properties["way_points"] = [0, 35, 10]
    with pytest.raises(RoutingResponseInvalid):
        normalize_ors_geojson(result, POINTS)


def test_transport_counts_attempts_and_never_sends_a_fourth() -> None:
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert str(request.url) == DIRECTIONS_URL
        assert request.headers["Authorization"] == "test-key"
        return httpx.Response(200, json=payload())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        adapter = OpenRouteServiceAdapter("test-key", client)
        budget = RequestBudget(max_attempts=3)
        for _ in range(3):
            assert adapter.route(POINTS, budget).distance_meters == Decimal("1450.2")
        with pytest.raises(RoutingBudgetExceeded):
            adapter.route(POINTS, budget)
    assert budget.attempts == len(seen) == 3


def test_request_budget_rejects_looser_hard_policy_limits() -> None:
    with pytest.raises(ValueError):
        RequestBudget(max_attempts=5)
    assert RequestBudget().max_attempts == 4
    assert RequestBudget().deadline_seconds == 15


def test_default_budget_counts_four_calls_and_rejects_fifth() -> None:
    budget = RequestBudget()
    for _ in range(4):
        budget.consume_attempt()
    with pytest.raises(RoutingBudgetExceeded):
        budget.consume_attempt()
    assert budget.attempts == 4


@pytest.mark.parametrize(
    ("status", "code", "error_type"),
    [
        (404, 2009, NoDrivableRoute),
        (400, 2004, RoutingLimitExceeded),
        (401, None, RoutingAuthenticationFailed),
        (429, None, RoutingUnavailable),
        (503, None, RoutingUnavailable),
    ],
)
def test_provider_error_translation_and_attempt_count(
    status: int, code: int | None, error_type: type[Exception]
) -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"code": code}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        budget = RequestBudget()
        adapter = OpenRouteServiceAdapter("test-key", client)
        with pytest.raises(error_type):
            adapter.route(POINTS, budget)
    assert budget.attempts == 1


def test_invalid_waypoint_count_does_not_consume_attempt() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    ) as client:
        adapter = OpenRouteServiceAdapter("test-key", client)
        budget = RequestBudget()
        with pytest.raises(ValueError):
            adapter.route((POINTS[0],), budget)
    assert budget.attempts == 0


@pytest.mark.parametrize("expired", (False, True))
def test_provider_timeout_preserves_recovery_only_before_absolute_deadline(expired):
    clock = [0.0]
    budget = RequestBudget(clock=lambda: clock[0])

    def timeout(request):
        clock[0] = 16.0 if expired else 6.0
        raise httpx.ReadTimeout("simulated timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        adapter = OpenRouteServiceAdapter("test-key", client)
        with pytest.raises(PlanningDeadlineExceeded if expired else RoutingUnavailable):
            adapter.route(POINTS, budget)
    assert budget.attempts == 1


def matrix_payload() -> dict:
    return {
        "distances": [[0, 1200.5, None], [1350.25, 0, 600], [None, 580, 0]],
        "durations": [[0, 91.5, None], [105.25, 0, 46], [None, 44, 0]],
        "sources": [
            {"location": list(point.as_provider_pair()), "snapped_distance": 2.5}
            for point in POINTS
        ],
        "destinations": [
            {"location": list(point.as_provider_pair()), "snapped_distance": 3.5}
            for point in POINTS
        ],
    }


def test_matrix_preserves_direction_and_counts_against_route_budget() -> None:
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if str(request.url) == MATRIX_URL:
            assert json.loads(request.content) == {
                "locations": [list(point.as_provider_pair()) for point in POINTS],
                "metrics": ["distance", "duration"],
            }
            return httpx.Response(200, json=matrix_payload())
        assert str(request.url) == DIRECTIONS_URL
        return httpx.Response(200, json=payload())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        adapter = OpenRouteServiceAdapter("test-key", client)
        budget = RequestBudget(max_attempts=2)
        matrix = adapter.matrix(POINTS, budget)
        assert matrix.distances_meters[0][1] == Decimal("1200.5")
        assert matrix.distances_meters[1][0] == Decimal("1350.25")
        assert matrix.distances_meters[0][2] is None
        assert matrix.durations_seconds[0][1] == Decimal("91.5")
        assert matrix.source_snap_meters == (Decimal("2.5"),) * 3
        adapter.route(POINTS, budget)
        with pytest.raises(RoutingBudgetExceeded):
            adapter.matrix(POINTS, budget)
    assert seen == [MATRIX_URL, DIRECTIONS_URL]


@pytest.mark.parametrize(
    "mutation",
    [
        "rows",
        "columns",
        "negative",
        "snap",
        "coordinates",
        "missing_duration",
        "negative_duration",
        "reachability_mismatch",
    ],
)
def test_invalid_matrix_is_rejected(mutation: str) -> None:
    result = matrix_payload()
    if mutation == "rows":
        result["distances"].pop()
    elif mutation == "columns":
        result["distances"][0].pop()
    elif mutation == "negative":
        result["distances"][0][1] = -1
    elif mutation == "snap":
        result["sources"][0]["snapped_distance"] = None
    elif mutation == "missing_duration":
        del result["durations"]
    elif mutation == "negative_duration":
        result["durations"][0][1] = -1
    elif mutation == "reachability_mismatch":
        result["durations"][0][2] = 10
    else:
        result["sources"][0]["location"] = [-87.6, 120]
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=result))
    ) as client:
        adapter = OpenRouteServiceAdapter("test-key", client)
        with pytest.raises(RoutingResponseInvalid):
            adapter.matrix(POINTS, RequestBudget())


def test_matrix_accepts_omitted_zero_snap_only_at_same_location() -> None:
    result = matrix_payload()
    del result["sources"][0]["snapped_distance"]
    del result["destinations"][0]["snapped_distance"]
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=result))
    ) as client:
        matrix = OpenRouteServiceAdapter("test-key", client).matrix(
            POINTS, RequestBudget()
        )
    assert matrix.source_snap_meters[0] == 0
    assert matrix.destination_snap_meters[0] == 0

    result["sources"][0]["location"] = [-100, 40]
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=result))
    ) as client:
        with pytest.raises(RoutingResponseInvalid, match="omitted a nonzero snap"):
            OpenRouteServiceAdapter("test-key", client).matrix(POINTS, RequestBudget())


def test_matrix_limit_rejects_oversized_request_before_transport() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: pytest.fail("oversized matrix reached transport")
        )
    ) as client:
        with pytest.raises(RoutingRequestRejected, match="59 locations"):
            OpenRouteServiceAdapter("test-key", client).matrix(
                POINTS[:1] * 60, RequestBudget()
            )
        assert (
            OpenRouteServiceAdapter(
                "test-key", client, matrix_location_limit=40
            ).matrix_location_limit
            == 40
        )
        with pytest.raises(ValueError, match="public restrictions"):
            OpenRouteServiceAdapter("test-key", client, matrix_location_limit=60)
