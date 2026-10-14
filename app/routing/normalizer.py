"""Translate ORS GeoJSON to validated, provider-independent route records."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.routing import Coordinates, Route, RouteLeg, RouteWaypoint
from app.routing.geometry import geodesic_meters


class RoutingResponseInvalid(ValueError):
    pass


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise RoutingResponseInvalid(f"invalid {field}")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise RoutingResponseInvalid(f"invalid {field}") from error
    if not result.is_finite() or result < 0:
        raise RoutingResponseInvalid(f"invalid {field}")
    return result


def _coordinate(value: Any) -> Coordinates:
    if not isinstance(value, list) or len(value) < 2:
        raise RoutingResponseInvalid("invalid route coordinate")
    if any(
        isinstance(number, bool) or not isinstance(number, (int, float))
        for number in value[:2]
    ):
        raise RoutingResponseInvalid("invalid route coordinate")
    try:
        return Coordinates(latitude=float(value[1]), longitude=float(value[0]))
    except ValueError as error:
        raise RoutingResponseInvalid("invalid route coordinate") from error


def normalize_ors_geojson(
    payload: Any,
    requested: tuple[Coordinates, ...],
    fetched_at: datetime | None = None,
) -> Route:
    try:
        if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
            raise RoutingResponseInvalid("expected GeoJSON FeatureCollection")
        features = payload.get("features")
        if not isinstance(features, list) or len(features) != 1:
            raise RoutingResponseInvalid("expected exactly one route feature")
        feature = features[0]
        if not isinstance(feature, dict):
            raise RoutingResponseInvalid("invalid route feature")
        geometry_object = feature.get("geometry")
        if (
            not isinstance(geometry_object, dict)
            or geometry_object.get("type") != "LineString"
        ):
            raise RoutingResponseInvalid("expected LineString route geometry")
        raw_geometry = geometry_object.get("coordinates")
        if (
            not isinstance(raw_geometry, list)
            or len(raw_geometry) < 2
            or len(raw_geometry) > 250_000
        ):
            raise RoutingResponseInvalid("invalid route geometry size")
        geometry = tuple(_coordinate(point) for point in raw_geometry)
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise RoutingResponseInvalid("missing route properties")
        indexes = properties.get("way_points")
        raw_segments = properties.get("segments")
        if not isinstance(indexes, list) or len(indexes) != len(requested):
            raise RoutingResponseInvalid("waypoint count mismatch")
        if (
            not isinstance(raw_segments, list)
            or len(raw_segments) != len(requested) - 1
        ):
            raise RoutingResponseInvalid("route leg count mismatch")
        if any(
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index >= len(geometry)
            for index in indexes
        ):
            raise RoutingResponseInvalid("invalid waypoint geometry index")
        if indexes[0] != 0 or indexes[-1] != len(geometry) - 1:
            raise RoutingResponseInvalid("route endpoint indexes mismatch")
        if indexes != sorted(indexes):
            raise RoutingResponseInvalid("waypoint order mismatch")
        waypoints = tuple(
            RouteWaypoint(
                requested=original,
                routed=geometry[index],
                geometry_index=index,
                snap_distance_meters=geodesic_meters(original, geometry[index]),
            )
            for original, index in zip(requested, indexes, strict=True)
        )
        legs = []
        for segment in raw_segments:
            if not isinstance(segment, dict):
                raise RoutingResponseInvalid("invalid route leg")
            legs.append(
                RouteLeg(
                    distance_meters=_decimal(segment.get("distance"), "leg distance"),
                    duration_seconds=_decimal(segment.get("duration"), "leg duration"),
                )
            )
        summary = properties.get("summary")
        if not isinstance(summary, dict):
            raise RoutingResponseInvalid("missing route summary")
        distance = _decimal(summary.get("distance"), "route distance")
        duration = _decimal(summary.get("duration"), "route duration")
        tolerance = max(Decimal("1"), Decimal("0.1") * len(legs))
        if (
            abs(distance - sum((leg.distance_meters for leg in legs), Decimal(0)))
            > tolerance
        ):
            raise RoutingResponseInvalid("route and leg distances disagree")
        if (
            abs(duration - sum((leg.duration_seconds for leg in legs), Decimal(0)))
            > tolerance
        ):
            raise RoutingResponseInvalid("route and leg durations disagree")
        metadata = payload.get("metadata")
        engine = metadata.get("engine") if isinstance(metadata, dict) else None
        engine_version = engine.get("version") if isinstance(engine, dict) else None
        if engine_version is not None and not isinstance(engine_version, str):
            raise RoutingResponseInvalid("invalid engine version")
        return Route(
            geometry=geometry,
            waypoints=waypoints,
            legs=tuple(legs),
            distance_meters=distance,
            duration_seconds=duration,
            engine_version=engine_version,
            fetched_at=fetched_at or datetime.now(UTC),
        )
    except (KeyError, TypeError, IndexError, ValueError) as error:
        if isinstance(error, RoutingResponseInvalid):
            raise
        raise RoutingResponseInvalid("invalid provider route response") from error
