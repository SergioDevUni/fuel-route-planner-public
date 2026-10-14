"""Directed road matrix from a locally prepared Valhalla tile graph."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from app.domain.road import RoadDistanceMatrix
from app.domain.routing import Coordinates, Route, RouteLeg, RouteWaypoint
from app.planning.budget import RequestBudget
from app.routing.geometry import geodesic_meters


class LocalValhallaUnavailable(ValueError):
    pass


class LocalValhallaSnapFailed(LocalValhallaUnavailable):
    pass


class LocalValhallaNoPath(LocalValhallaUnavailable):
    pass


@dataclass(frozen=True, slots=True)
class LocalRoadBlock:
    distances_meters: tuple[tuple[Decimal | None, ...], ...]
    durations_seconds: tuple[tuple[Decimal | None, ...], ...]


def _nonnegative(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise LocalValhallaUnavailable(f"invalid {field}")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise LocalValhallaUnavailable(f"invalid {field}") from error
    if not number.is_finite() or number < 0:
        raise LocalValhallaUnavailable(f"invalid {field}")
    return number


class LocalValhalla:
    supports_shorter_routes = True

    def __init__(
        self,
        base_url: str,
        graph_version: str,
        *,
        max_points: int = 20,
        max_snap_meters: Decimal = Decimal(1000),
        request_timeout_seconds: float = 6,
        client: httpx.Client | None = None,
    ) -> None:
        if not graph_version or not 2 <= max_points <= 500:
            raise ValueError("graph version and matrix point limit are required")
        if not max_snap_meters.is_finite() or max_snap_meters <= 0:
            raise ValueError("snap limit must be positive")
        if not math.isfinite(request_timeout_seconds) or request_timeout_seconds <= 0:
            raise ValueError("request timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.graph_version = graph_version
        self.max_points = max_points
        self.max_snap_meters = max_snap_meters
        self.request_timeout_seconds = request_timeout_seconds
        self._client = client or httpx.Client(timeout=6, trust_env=False)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def road_leg(
        self, source: Coordinates, target: Coordinates, budget: RequestBudget
    ) -> RouteLeg:
        """Get one exact routed leg without decoding a display geometry."""
        response = self._post(
            "route",
            {
                "locations": [
                    {"lat": source.latitude, "lon": source.longitude},
                    {"lat": target.latitude, "lon": target.longitude},
                ],
                "costing": "auto",
                "format": "osrm",
                "shape_format": "no_shape",
                "directions_type": "none",
                "units": "kilometers",
            },
            budget,
        )
        if not isinstance(response, dict):
            raise LocalValhallaUnavailable("local road leg response is invalid")
        try:
            summary = response["routes"][0]
            return RouteLeg(
                _nonnegative(summary["distance"], "road leg distance"),
                _nonnegative(summary["duration"], "road leg duration"),
            )
        except (IndexError, KeyError, TypeError) as error:
            raise LocalValhallaUnavailable(
                "local road leg summary is invalid"
            ) from error

    def shorter_route(
        self, points: tuple[Coordinates, ...], budget: RequestBudget
    ) -> Route:
        return self.route(points, budget, shortest=True)

    def route(
        self,
        points: tuple[Coordinates, ...],
        budget: RequestBudget,
        *,
        shortest: bool = False,
    ) -> Route:
        """Get final road geometry and per-stop legs from the same graph/profile."""
        if len(points) < 2:
            raise ValueError("local route needs at least two waypoints")
        if len(points) <= self.max_points:
            return self._route_chunk(points, budget, shortest=shortest)
        geometry: list[Coordinates] = []
        waypoints: list[RouteWaypoint] = []
        legs: list[RouteLeg] = []
        offset = 0
        while offset < len(points) - 1:
            section = points[offset : offset + self.max_points]
            partial = self._route_chunk(section, budget, shortest=shortest)
            if geometry and geometry[-1] == partial.geometry[0]:
                shift = len(geometry) - 1
                geometry.extend(partial.geometry[1:])
            else:
                shift = len(geometry)
                geometry.extend(partial.geometry)
            waypoints.extend(
                RouteWaypoint(
                    waypoint.requested,
                    waypoint.routed,
                    waypoint.geometry_index + shift,
                    waypoint.snap_distance_meters,
                )
                for waypoint in partial.waypoints[1 if offset else 0 :]
            )
            legs.extend(partial.legs)
            offset += len(section) - 1
        return Route(
            tuple(geometry),
            tuple(waypoints),
            tuple(legs),
            sum((leg.distance_meters for leg in legs), Decimal(0)),
            sum((leg.duration_seconds for leg in legs), Decimal(0)),
            f"valhalla:{self.graph_version}:auto" + (":shorter" if shortest else ""),
            datetime.now(UTC),
        )

    def _route_chunk(
        self,
        points: tuple[Coordinates, ...],
        budget: RequestBudget,
        *,
        shortest: bool = False,
    ) -> Route:
        response = self._post(
            "route",
            {
                "locations": [
                    {"lat": point.latitude, "lon": point.longitude} for point in points
                ],
                "costing": "auto",
                "format": "osrm",
                "shape_format": "geojson",
                "directions_type": "none",
                "units": "kilometers",
                **(
                    {"costing_options": {"auto": {"shortest": True}}}
                    if shortest
                    else {}
                ),
            },
            budget,
        )
        if not isinstance(response, dict):
            raise LocalValhallaUnavailable("local route response is invalid")
        raw_routes = response.get("routes")
        raw_waypoints = response.get("waypoints")
        if (
            not isinstance(raw_routes, list)
            or not raw_routes
            or not isinstance(raw_waypoints, list)
            or len(raw_waypoints) != len(points)
        ):
            raise LocalValhallaUnavailable("local route has no matching waypoints")
        chosen = raw_routes[0]
        if not isinstance(chosen, dict):
            raise LocalValhallaUnavailable("local route payload is invalid")
        raw_geometry = chosen.get("geometry")
        raw_legs = chosen.get("legs")
        if (
            not isinstance(raw_geometry, dict)
            or raw_geometry.get("type") != "LineString"
            or not isinstance(raw_geometry.get("coordinates"), list)
            or not isinstance(raw_legs, list)
            or len(raw_legs) != len(points) - 1
        ):
            raise LocalValhallaUnavailable("local route shape or legs are invalid")
        try:
            geometry = tuple(
                Coordinates(float(pair[1]), float(pair[0]))
                for pair in raw_geometry["coordinates"]
            )
            waypoints = []
            for requested, raw in zip(points, raw_waypoints, strict=True):
                if not isinstance(raw, dict):
                    raise ValueError("invalid waypoint")
                pair = raw["location"]
                routed = Coordinates(float(pair[1]), float(pair[0]))
                snap = _nonnegative(raw["distance"], "route snap")
                geometry_index = min(
                    range(len(geometry)),
                    key=lambda index: geodesic_meters(geometry[index], routed),
                )
                waypoints.append(RouteWaypoint(requested, routed, geometry_index, snap))
            legs = tuple(
                RouteLeg(
                    _nonnegative(raw["distance"], "route distance"),
                    _nonnegative(raw["duration"], "route duration"),
                )
                for raw in raw_legs
            )
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise LocalValhallaUnavailable("local route geometry is invalid") from error
        if len(geometry) < 2:
            raise LocalValhallaUnavailable("local route geometry is too short")
        return Route(
            geometry,
            tuple(waypoints),
            legs,
            sum((leg.distance_meters for leg in legs), Decimal(0)),
            sum((leg.duration_seconds for leg in legs), Decimal(0)),
            f"valhalla:{self.graph_version}:auto" + (":shorter" if shortest else ""),
            datetime.now(UTC),
        )

    def _post(
        self, action: str, payload: dict[str, object], budget: RequestBudget
    ) -> object:
        budget.check_deadline()
        try:
            response = self._client.post(
                f"{self.base_url}/{action}",
                json=payload,
                timeout=min(
                    self.request_timeout_seconds, max(0.1, budget.remaining_seconds)
                ),
            )
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPStatusError as error:
            try:
                code = error.response.json().get("error_code")
            except (ValueError, AttributeError):
                code = None
            if code in (170, 441, 442):
                raise LocalValhallaNoPath(
                    "no drivable path between road points"
                ) from error
            raise LocalValhallaUnavailable(f"local {action} is unavailable") from error
        except (httpx.HTTPError, ValueError) as error:
            raise LocalValhallaUnavailable(f"local {action} is unavailable") from error
        budget.check_deadline()
        return result

    def matrix(
        self, points: tuple[Coordinates, ...], budget: RequestBudget
    ) -> RoadDistanceMatrix:
        if not 2 <= len(points) <= self.max_points:
            raise ValueError("local matrix point count exceeds configured limit")
        snaps = self.locate(points, budget)
        block = self.matrix_block(points, points, budget)
        return RoadDistanceMatrix(
            block.distances_meters, snaps, snaps, block.durations_seconds
        )

    def locate(
        self, points: tuple[Coordinates, ...], budget: RequestBudget
    ) -> tuple[Decimal, ...]:
        if not 1 <= len(points) <= self.max_points:
            raise ValueError("local locate point count exceeds configured limit")
        locations = [
            {"lat": point.latitude, "lon": point.longitude} for point in points
        ]
        located = self._post(
            "locate", {"locations": locations, "costing": "auto"}, budget
        )
        if not isinstance(located, list) or len(located) != len(points):
            raise LocalValhallaUnavailable("local locate response has wrong size")
        snaps: list[Decimal] = []
        for point, location in zip(points, located, strict=True):
            if not isinstance(location, dict):
                raise LocalValhallaSnapFailed("local road point did not correlate")
            edges = location.get("edges")
            if (
                not isinstance(edges, list)
                or not edges
                or not isinstance(edges[0], dict)
            ):
                raise LocalValhallaSnapFailed("local road point has no drivable edge")
            edge = edges[0]
            latitude = edge.get("correlated_lat")
            longitude = edge.get("correlated_lon")
            if (
                isinstance(latitude, bool)
                or isinstance(longitude, bool)
                or not isinstance(latitude, (int, float))
                or not isinstance(longitude, (int, float))
                or not math.isfinite(latitude)
                or not math.isfinite(longitude)
            ):
                raise LocalValhallaUnavailable("local road correlation is invalid")
            try:
                snapped = Coordinates(float(latitude), float(longitude))
            except ValueError as error:
                raise LocalValhallaUnavailable(
                    "local road correlation is invalid"
                ) from error
            distance = geodesic_meters(point, snapped)
            if distance > self.max_snap_meters:
                raise LocalValhallaSnapFailed("point is outside the local road graph")
            snaps.append(distance)
        return tuple(snaps)

    def matrix_block(
        self,
        sources: tuple[Coordinates, ...],
        targets: tuple[Coordinates, ...],
        budget: RequestBudget,
    ) -> LocalRoadBlock:
        """Query a directed rectangular block for offline station-graph prep."""
        if (
            not sources
            or not targets
            or len(sources) > self.max_points
            or len(targets) > self.max_points
        ):
            raise ValueError("local matrix block exceeds configured point limit")
        result = self._post(
            "sources_to_targets",
            {
                "sources": [
                    {"lat": point.latitude, "lon": point.longitude} for point in sources
                ],
                "targets": [
                    {"lat": point.latitude, "lon": point.longitude} for point in targets
                ],
                "costing": "auto",
                "units": "kilometers",
                "verbose": False,
            },
            budget,
        )
        if not isinstance(result, dict):
            raise LocalValhallaUnavailable("local matrix response is invalid")
        values = result.get("sources_to_targets")
        if not isinstance(values, dict):
            raise LocalValhallaUnavailable("local matrix values are missing")

        def parse_rows(key: str, scale: int) -> tuple[tuple[Decimal | None, ...], ...]:
            raw = values.get(key)
            if not isinstance(raw, list) or len(raw) != len(sources):
                raise LocalValhallaUnavailable(f"local matrix {key} shape is invalid")
            rows: list[tuple[Decimal | None, ...]] = []
            for row in raw:
                if not isinstance(row, list) or len(row) != len(targets):
                    raise LocalValhallaUnavailable(
                        f"local matrix {key} row shape is invalid"
                    )
                rows.append(
                    tuple(
                        None if value is None else _nonnegative(value, key) * scale
                        for value in row
                    )
                )
            return tuple(rows)

        distances = parse_rows("distances", 1000)
        durations = parse_rows("durations", 1)
        if any(
            (distance is None) != (duration is None)
            for distance_row, duration_row in zip(distances, durations, strict=True)
            for distance, duration in zip(distance_row, duration_row, strict=True)
        ):
            raise LocalValhallaUnavailable(
                "local road distance/time reachability differs"
            )
        return LocalRoadBlock(distances, durations)


class BatchedLocalValhalla:
    """Assemble a larger directed matrix from bounded local rectangles."""

    def __init__(self, local: LocalValhalla) -> None:
        self.local = local

    def matrix(
        self, points: tuple[Coordinates, ...], budget: RequestBudget
    ) -> RoadDistanceMatrix:
        if len(points) < 2:
            raise ValueError("a road matrix needs at least two points")
        batch = self.local.max_points
        snaps: list[Decimal] = []
        for offset in range(0, len(points), batch):
            snaps.extend(self.local.locate(points[offset : offset + batch], budget))
        distances: list[list[Decimal | None]] = [[None] * len(points) for _ in points]
        durations: list[list[Decimal | None]] = [[None] * len(points) for _ in points]
        for source_offset in range(0, len(points), batch):
            sources = points[source_offset : source_offset + batch]
            for target_offset in range(0, len(points), batch):
                targets = points[target_offset : target_offset + batch]
                block = self.local.matrix_block(sources, targets, budget)
                for row_index, (distance_row, duration_row) in enumerate(
                    zip(
                        block.distances_meters,
                        block.durations_seconds,
                        strict=True,
                    )
                ):
                    source = source_offset + row_index
                    for column, (distance, duration) in enumerate(
                        zip(distance_row, duration_row, strict=True)
                    ):
                        target = target_offset + column
                        distances[source][target] = distance
                        durations[source][target] = duration
        return RoadDistanceMatrix(
            tuple(tuple(row) for row in distances),
            tuple(snaps),
            tuple(snaps),
            tuple(tuple(row) for row in durations),
        )
