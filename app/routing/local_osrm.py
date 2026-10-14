"""Directed road distances from a locally hosted OSRM car-profile graph."""

import math
from dataclasses import dataclass

import httpx

from app.domain.routing import Coordinates


class LocalRoadUnavailable(ValueError):
    pass


class LocalRoadSnapFailed(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RoadMatrix:
    distances_meters: tuple[tuple[float | None, ...], ...]
    snap_meters: tuple[float, ...]
    graph_version: str
    distance_semantics: str = "distance_of_fastest_car_route"
    durations_seconds: tuple[tuple[float | None, ...], ...] = ()
    destination_snap_meters: tuple[float, ...] = ()


class LocalOSRM:
    def __init__(
        self,
        base_url: str,
        graph_version: str,
        *,
        max_snap_meters: float = 250,
        client: httpx.Client | None = None,
    ) -> None:
        if not graph_version or not 0 < max_snap_meters <= 1_000:
            raise ValueError("graph version and bounded snap limit are required")
        self.base_url = base_url.rstrip("/")
        self.graph_version = graph_version
        self.max_snap_meters = max_snap_meters
        self._client = client or httpx.Client(timeout=2, trust_env=False)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def matrix(self, points: tuple[Coordinates, ...]) -> RoadMatrix:
        if not 2 <= len(points) <= 100:
            raise ValueError("local road matrix requires 2 to 100 points")
        coordinates = ";".join(
            f"{point.longitude},{point.latitude}" for point in points
        )
        try:
            response = self._client.get(
                f"{self.base_url}/table/v1/driving/{coordinates}",
                params={"annotations": "distance,duration"},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise LocalRoadUnavailable("local road matrix is unavailable") from error
        if not isinstance(payload, dict) or payload.get("code") != "Ok":
            raise LocalRoadUnavailable("local road matrix rejected the points")
        raw_sources = payload.get("sources")
        raw_destinations = payload.get("destinations")
        raw_distances = payload.get("distances")
        raw_durations = payload.get("durations")
        size = len(points)
        if (
            not isinstance(raw_sources, list)
            or len(raw_sources) != size
            or not isinstance(raw_destinations, list)
            or len(raw_destinations) != size
            or not isinstance(raw_distances, list)
            or len(raw_distances) != size
            or not isinstance(raw_durations, list)
            or len(raw_durations) != size
        ):
            raise LocalRoadUnavailable("local road matrix shape is invalid")

        def snaps(waypoints: list[object]) -> tuple[float, ...]:
            result: list[float] = []
            for waypoint in waypoints:
                value = waypoint.get("distance") if isinstance(waypoint, dict) else None
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise LocalRoadUnavailable("local road snap is invalid")
                if value > self.max_snap_meters:
                    raise LocalRoadSnapFailed("point is outside the local road graph")
                result.append(float(value))
            return tuple(result)

        def rows(raw: list[object], label: str) -> tuple[tuple[float | None, ...], ...]:
            result: list[tuple[float | None, ...]] = []
            for row in raw:
                if not isinstance(row, list) or len(row) != size:
                    raise LocalRoadUnavailable(
                        f"local road {label} row shape is invalid"
                    )
                parsed: list[float | None] = []
                for value in row:
                    if value is None:
                        parsed.append(None)
                    elif (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        or value < 0
                    ):
                        raise LocalRoadUnavailable(f"local road {label} is invalid")
                    else:
                        parsed.append(float(value))
                result.append(tuple(parsed))
            return tuple(result)

        distances = rows(raw_distances, "distance")
        durations = rows(raw_durations, "duration")
        if any(
            (distance is None) != (duration is None)
            for distance_row, duration_row in zip(distances, durations, strict=True)
            for distance, duration in zip(distance_row, duration_row, strict=True)
        ):
            raise LocalRoadUnavailable("local road reachability differs by metric")
        return RoadMatrix(
            distances,
            snaps(raw_sources),
            self.graph_version,
            durations_seconds=durations,
            destination_snap_meters=snaps(raw_destinations),
        )
