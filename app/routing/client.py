"""Bounded synchronous ORS transport. Every outbound attempt consumes budget."""

import json
import math
from decimal import Decimal
from typing import Any

import httpx

from app.domain.road import RoadDistanceMatrix
from app.domain.routing import Coordinates, Route
from app.planning.budget import RequestBudget
from app.planning.ports import MAX_HOSTED_MATRIX_LOCATIONS, MAX_PUBLIC_MATRIX_LOCATIONS
from app.routing.curl_transport import CurlTransport
from app.routing.geometry import geodesic_meters
from app.routing.normalizer import RoutingResponseInvalid, normalize_ors_geojson

DIRECTIONS_URL = (
    "https://api.heigit.org/openrouteservice/v2/directions/driving-car/geojson"
)
MATRIX_URL = "https://api.heigit.org/openrouteservice/v2/matrix/driving-car"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
NO_ROUTE_CODES = {2009, 2010, 2013, 2014, 2015, 2016}


class RoutingUnavailable(RuntimeError):
    pass


class RoutingTransportUnavailable(RoutingUnavailable):
    """Transient transport failure, distinct from credentials and quota status."""


class RoutingQuotaExceeded(RoutingUnavailable):
    """Provider quota exhaustion requires pausing benchmark traffic."""


class NoDrivableRoute(ValueError):
    pass


class RoutingRequestRejected(ValueError):
    pass


class RoutingLimitExceeded(RoutingRequestRejected):
    pass


class RoutingAuthenticationFailed(RoutingUnavailable):
    pass


def _provider_error_code(body: bytes) -> int | None:
    try:
        payload: Any = json.loads(body)
        error = payload.get("error") if isinstance(payload, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        return code if isinstance(code, int) and not isinstance(code, bool) else None
    except (ValueError, TypeError):
        return None


def _provider_quota_exceeded(body: bytes) -> bool:
    """The hosted gateway can report exhausted quota with HTTP 403."""
    try:
        payload = json.loads(body)
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("message") if isinstance(error, dict) else error
        return (
            isinstance(message, str) and message.strip().casefold() == "quota exceeded"
        )
    except (ValueError, TypeError):
        return False


class OpenRouteServiceAdapter:
    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        *,
        matrix_location_limit: int = MAX_HOSTED_MATRIX_LOCATIONS,
    ) -> None:
        if not api_key.strip():
            raise ValueError("ORS_API_KEY is required")
        if not 2 <= matrix_location_limit <= MAX_PUBLIC_MATRIX_LOCATIONS:
            raise ValueError("matrix location limit is outside public restrictions")
        self._api_key = api_key.strip()
        self._client = client or httpx.Client(
            transport=CurlTransport(max_response_bytes=MAX_RESPONSE_BYTES),
            trust_env=False,
        )
        self._owns_client = client is None
        self.matrix_location_limit = matrix_location_limit

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _post(
        self, url: str, body: dict[str, Any], budget: RequestBudget
    ) -> dict[str, Any]:
        budget.consume_attempt()
        remaining = budget.remaining_seconds
        timeout = httpx.Timeout(
            connect=min(2.0, remaining),
            read=min(10.0, remaining),
            write=min(6.0, remaining),
            pool=min(0.5, remaining),
        )
        try:
            with self._client.stream(
                "POST",
                url,
                headers={"Authorization": self._api_key},
                json=body,
                timeout=timeout,
                extensions={"routing_deadline_check": budget.check_deadline},
            ) as response:
                chunks: list[bytes] = []
                total_bytes = 0
                for chunk in response.iter_bytes():
                    budget.check_deadline()
                    total_bytes += len(chunk)
                    if total_bytes > MAX_RESPONSE_BYTES:
                        raise RoutingResponseInvalid(
                            "routing response exceeds size limit"
                        )
                    chunks.append(chunk)
                raw = b"".join(chunks)
                status = response.status_code
        except httpx.TimeoutException as error:
            # A per-call I/O timeout need not exhaust the shared request.
            # Preserve its remaining time for a verified fallback; expiry
            # still propagates as the absolute planning deadline.
            budget.check_deadline()
            raise RoutingTransportUnavailable("routing provider timed out") from error
        except httpx.HTTPError as error:
            raise RoutingTransportUnavailable(
                "routing provider connection failed"
            ) from error
        budget.check_deadline()
        if status != 200:
            code = _provider_error_code(raw)
            if status == 429 or (status == 403 and _provider_quota_exceeded(raw)):
                raise RoutingQuotaExceeded("routing provider quota exhausted")
            if status >= 500:
                raise RoutingUnavailable(
                    "routing provider unavailable or quota exhausted"
                )
            if status in (401, 403):
                raise RoutingAuthenticationFailed("routing key was rejected")
            if status == 413 or code == 2004:
                raise RoutingLimitExceeded("routing provider limit exceeded")
            if code in NO_ROUTE_CODES:
                raise NoDrivableRoute("provider found no drivable route")
            raise RoutingRequestRejected("provider rejected the routing request")
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise RoutingResponseInvalid("provider returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise RoutingResponseInvalid("provider returned a non-object response")
        return payload

    def route(self, waypoints: tuple[Coordinates, ...], budget: RequestBudget) -> Route:
        if not 2 <= len(waypoints) <= 50:
            raise RoutingRequestRejected("routing requires 2 to 50 waypoints")
        with budget.stage("directions_transport_and_decode"):
            payload = self._post(
                DIRECTIONS_URL,
                {
                    "coordinates": [
                        list(point.as_provider_pair()) for point in waypoints
                    ],
                    "radiuses": [1000, *([250] * (len(waypoints) - 2)), 1000],
                },
                budget,
            )
        with budget.stage("directions_normalization"):
            return normalize_ors_geojson(payload, waypoints)

    def matrix(
        self, points: tuple[Coordinates, ...], budget: RequestBudget
    ) -> RoadDistanceMatrix:
        if not 2 <= len(points) <= self.matrix_location_limit:
            raise RoutingRequestRejected(
                f"matrix requires 2 to {self.matrix_location_limit} locations"
            )
        payload = self._post(
            MATRIX_URL,
            {
                "locations": [list(point.as_provider_pair()) for point in points],
                "metrics": ["distance", "duration"],
            },
            budget,
        )
        size = len(points)

        def values(field: str) -> tuple[tuple[Decimal | None, ...], ...]:
            raw_rows = payload.get(field)
            if not isinstance(raw_rows, list) or len(raw_rows) != size:
                raise RoutingResponseInvalid(f"matrix {field} row count differs")
            rows = []
            for raw_row in raw_rows:
                if not isinstance(raw_row, list) or len(raw_row) != size:
                    raise RoutingResponseInvalid(f"matrix {field} column count differs")
                row: list[Decimal | None] = []
                for value in raw_row:
                    if value is None:
                        row.append(None)
                    elif (
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and math.isfinite(value)
                        and value >= 0
                    ):
                        row.append(Decimal(str(value)))
                    else:
                        raise RoutingResponseInvalid(
                            f"matrix {field} has invalid value"
                        )
                rows.append(tuple(row))
            return tuple(rows)

        distances = values("distances")
        durations = values("durations")
        if any(
            (distance is None) != (duration is None)
            for distance_row, duration_row in zip(distances, durations, strict=True)
            for distance, duration in zip(distance_row, duration_row, strict=True)
        ):
            raise RoutingResponseInvalid(
                "matrix distance and duration reachability differ"
            )

        def snaps(field: str) -> tuple[Decimal, ...]:
            entries = payload.get(field)
            if not isinstance(entries, list) or len(entries) != size:
                raise RoutingResponseInvalid(f"matrix {field} differs from locations")
            result = []
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    raise RoutingResponseInvalid(f"matrix {field} has invalid point")
                value = entry.get("snapped_distance")
                location = entry.get("location")
                if (
                    not isinstance(location, list)
                    or len(location) != 2
                    or any(
                        not isinstance(item, (int, float))
                        or isinstance(item, bool)
                        or not math.isfinite(item)
                        for item in location
                    )
                ):
                    raise RoutingResponseInvalid(f"matrix {field} has invalid snap")
                try:
                    snapped = Coordinates(float(location[1]), float(location[0]))
                except ValueError as error:
                    raise RoutingResponseInvalid(
                        f"matrix {field} has invalid coordinates"
                    ) from error
                if "snapped_distance" not in entry:
                    # ORS can omit this field when a location already sits on
                    # the road graph. Accept only a near-identical returned
                    # coordinate, never an unknown large snap.
                    distance = geodesic_meters(points[index], snapped)
                    if distance > 1:
                        raise RoutingResponseInvalid(
                            f"matrix {field} omitted a nonzero snap"
                        )
                    result.append(distance)
                elif (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and value >= 0
                ):
                    result.append(Decimal(str(value)))
                else:
                    raise RoutingResponseInvalid(f"matrix {field} has invalid snap")
            return tuple(result)

        return RoadDistanceMatrix(
            distances, snaps("sources"), snaps("destinations"), durations
        )
