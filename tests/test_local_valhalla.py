"""Local Valhalla matrix preserves directed road distance and CSV access."""

import httpx
import pytest

from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.routing.local_valhalla import (
    BatchedLocalValhalla,
    LocalValhalla,
    LocalValhallaNoPath,
    LocalValhallaSnapFailed,
)


def test_local_matrix_parses_directed_distance_and_duration() -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/locate":
            return httpx.Response(
                200,
                json=[
                    {"edges": [{"correlated_lat": 40.0, "correlated_lon": -90.0}]},
                    {"edges": [{"correlated_lat": 40.1, "correlated_lon": -90.1}]},
                ],
            )
        return httpx.Response(
            200,
            json={
                "sources_to_targets": {
                    "distances": [[0, 12.345], [13.5, 0]],
                    "durations": [[0, 600], [700, 0]],
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = LocalValhalla("http://localhost:8002", "test-graph", client=client)
    result = adapter.matrix(
        (Coordinates(40, -90), Coordinates(40.1, -90.1)), RequestBudget()
    )
    assert requests == ["/locate", "/sources_to_targets"]
    assert result.distances_meters[0][1] == 12345
    assert result.distances_meters[1][0] == 13500
    assert result.durations_seconds is not None
    assert result.durations_seconds[1][0] == 700
    assert result.source_snap_meters == (0, 0)
    assert result.destination_snap_meters == (0, 0)


def test_local_matrix_rejects_unroutable_csv_point() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=[{"edges": []}, {"edges": []}])
        )
    )
    adapter = LocalValhalla("http://localhost:8002", "test-graph", client=client)
    with pytest.raises(LocalValhallaSnapFailed):
        adapter.matrix(
            (Coordinates(40, -90), Coordinates(40.1, -90.1)), RequestBudget()
        )


def test_rectangular_local_block_keeps_directional_shape() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "sources_to_targets": {
                        "distances": [[1.2, 3.4]],
                        "durations": [[100, 200]],
                    }
                },
            )
        )
    )
    adapter = LocalValhalla("http://localhost:8002", "test-graph", client=client)
    result = adapter.matrix_block(
        (Coordinates(40, -90),),
        (Coordinates(40.1, -90.1), Coordinates(40.2, -90.2)),
        RequestBudget(),
    )
    assert result.distances_meters == ((1200, 3400),)
    assert result.durations_seconds == ((100, 200),)


def test_direct_road_leg_repairs_a_blank_matrix_cell() -> None:
    import json

    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, json={"routes": [{"distance": 595922.25, "duration": 20000}]}
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = LocalValhalla("http://localhost:8002", "test-graph", client=client)
    leg = adapter.road_leg(
        Coordinates(24.7, -81), Coordinates(27.9, -82.7), RequestBudget()
    )
    assert leg.distance_meters == 595922.25
    assert leg.duration_seconds == 20000
    assert requests[0]["format"] == "osrm"
    assert requests[0]["shape_format"] == "no_shape"


def test_direct_road_leg_distinguishes_no_path_from_service_failure() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(400, json={"error_code": 170})
        )
    )
    adapter = LocalValhalla("http://localhost:8002", "test-graph", client=client)
    with pytest.raises(LocalValhallaNoPath):
        adapter.road_leg(
            Coordinates(24.7, -81), Coordinates(27.9, -82.7), RequestBudget()
        )


def test_local_route_uses_osrm_geojson_legs_and_snap_distance() -> None:
    response = {
        "routes": [
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-90.0, 40.0], [-89.95, 40.05], [-89.9, 40.1]],
                },
                "legs": [{"distance": 12000, "duration": 700}],
            }
        ],
        "waypoints": [
            {"location": [-90.0, 40.0], "distance": 0},
            {"location": [-89.9, 40.1], "distance": 12},
        ],
    }
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    )
    adapter = LocalValhalla("http://localhost:8002", "test-graph", client=client)
    result = adapter.route(
        (Coordinates(40, -90), Coordinates(40.1, -89.9)), RequestBudget()
    )
    assert result.distance_meters == 12000
    assert result.duration_seconds == 700
    assert result.waypoints[-1].snap_distance_meters == 12
    assert result.engine_version == "valhalla:test-graph:auto"
    assert len(result.geometry) == 3


def test_local_route_chunks_more_than_configured_waypoints() -> None:
    import json

    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        locations = json.loads(request.content)["locations"]
        calls.append(len(locations))
        coordinates = [[point["lon"], point["lat"]] for point in locations]
        return httpx.Response(
            200,
            json={
                "routes": [
                    {
                        "geometry": {
                            "type": "LineString",
                            "coordinates": coordinates,
                        },
                        "legs": [
                            {"distance": 1000, "duration": 60} for _ in locations[1:]
                        ],
                    }
                ],
                "waypoints": [
                    {"location": pair, "distance": 0} for pair in coordinates
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = LocalValhalla(
        "http://localhost:8002", "test-graph", max_points=2, client=client
    )
    points = tuple(Coordinates(40, -90 + i / 100) for i in range(4))
    result = adapter.route(points, RequestBudget())
    assert calls == [2, 2, 2]
    assert tuple(point.requested for point in result.waypoints) == points
    assert tuple(point.geometry_index for point in result.waypoints) == (0, 1, 2, 3)
    assert result.distance_meters == 3000
    assert result.duration_seconds == 180
    assert len(result.legs) == 3


def test_batched_matrix_preserves_direction_across_rectangles() -> None:
    import json

    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        calls.append(request.url.path)
        if request.url.path == "/locate":
            return httpx.Response(
                200,
                json=[
                    {
                        "edges": [
                            {
                                "correlated_lat": point["lat"],
                                "correlated_lon": point["lon"],
                            }
                        ]
                    }
                    for point in data["locations"]
                ],
            )
        distances = [
            [
                abs(source["lon"] - target["lon"]) + source["lon"] + 91
                for target in data["targets"]
            ]
            for source in data["sources"]
        ]
        return httpx.Response(
            200,
            json={
                "sources_to_targets": {
                    "distances": distances,
                    "durations": [[value * 10 for value in row] for row in distances],
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    local = LocalValhalla("http://localhost:8002", "test", max_points=2, client=client)
    points = (
        Coordinates(40, -90),
        Coordinates(40, -89),
        Coordinates(40, -88),
    )
    matrix = BatchedLocalValhalla(local).matrix(points, RequestBudget())
    assert len(matrix.distances_meters) == 3
    assert matrix.distances_meters[0][2] != matrix.distances_meters[2][0]
    assert calls.count("/locate") == 2
    assert calls.count("/sources_to_targets") == 4
