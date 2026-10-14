"""Provider-independent routed facts with explicit coordinate and distance units."""

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.units import finite_nonnegative


@dataclass(frozen=True, slots=True)
class Coordinates:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be finite and between -90 and 90")
        if not math.isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be finite and between -180 and 180")

    def as_provider_pair(self) -> tuple[float, float]:
        return self.longitude, self.latitude


@dataclass(frozen=True, slots=True)
class RouteWaypoint:
    requested: Coordinates
    routed: Coordinates
    geometry_index: int
    snap_distance_meters: Decimal

    def __post_init__(self) -> None:
        finite_nonnegative(self.snap_distance_meters, "snap_distance_meters")


@dataclass(frozen=True, slots=True)
class RouteLeg:
    distance_meters: Decimal
    duration_seconds: Decimal

    def __post_init__(self) -> None:
        finite_nonnegative(self.distance_meters, "distance_meters")
        finite_nonnegative(self.duration_seconds, "duration_seconds")


@dataclass(frozen=True, slots=True)
class Route:
    geometry: tuple[Coordinates, ...]
    waypoints: tuple[RouteWaypoint, ...]
    legs: tuple[RouteLeg, ...]
    distance_meters: Decimal
    duration_seconds: Decimal
    engine_version: str | None
    fetched_at: datetime

    def __post_init__(self) -> None:
        if len(self.geometry) < 2 or len(self.waypoints) < 2:
            raise ValueError("route needs geometry and two waypoints")
        if len(self.legs) != len(self.waypoints) - 1:
            raise ValueError("route leg count must match waypoint count")
        finite_nonnegative(self.distance_meters, "distance_meters")
        finite_nonnegative(self.duration_seconds, "duration_seconds")
