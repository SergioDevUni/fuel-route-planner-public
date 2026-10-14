"""Correlate, bound and observe public API requests before expensive work."""

import json
import logging
import uuid
from functools import lru_cache
from time import monotonic

from django.conf import settings
from django.core.exceptions import BadRequest, RequestDataTooBig
from django.db import DatabaseError

from app.api.deadline import planning_activity
from app.api.errors import api_error
from app.api.rate_limit import CallerRateLimiter

logger = logging.getLogger("app.api.operations")
BODY_LIMIT = 16 * 1024


@lru_cache(maxsize=8)
def caller_limiter(requests: int, seconds: int, callers: int) -> CallerRateLimiter:
    return CallerRateLimiter(requests, seconds, callers)


def endpoint_name(path: str) -> str | None:
    if path == "/api/v1/trips":
        return "trips"
    if path.startswith("/api/v1/trips/"):
        return "trip_result"
    if path.startswith("/maps/"):
        return "trip_map"
    if path in ("/health/live", "/health/ready"):
        return path.rsplit("/", 1)[-1]
    if path.startswith("/api/"):
        return "unknown_api"
    return None


class ApiBoundaryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        endpoint = endpoint_name(request.path_info)
        if endpoint is None:
            return self.get_response(request)
        request.api_request_id = uuid.uuid4().hex
        started = monotonic()
        response = self._guard(request, endpoint)
        if response is None:
            response = self.get_response(request)
        if response.status_code >= 400 and not response.get(
            "Content-Type", ""
        ).startswith("application/json"):
            codes = {
                400: ("INVALID_REQUEST", "Request is invalid."),
                403: ("FORBIDDEN", "Request is not permitted."),
                404: ("NOT_FOUND", "Endpoint was not found."),
                405: ("METHOD_NOT_ALLOWED", "HTTP method is not supported."),
                500: ("INTERNAL_ERROR", "The request could not be completed."),
            }
            code, message = codes.get(
                response.status_code,
                ("REQUEST_RETRY_REQUIRED", "Please try this request again."),
            )
            original = response
            response = api_error(
                code, message, original.status_code, request.api_request_id
            )
            for header in ("Allow", "Retry-After"):
                if header in original:
                    response[header] = original[header]
        response["X-Request-ID"] = request.api_request_id
        response["Cache-Control"] = "no-store"
        error_code = ""
        if response.status_code >= 400 and response.get("Content-Type", "").startswith(
            "application/json"
        ):
            error_code = json.loads(response.content).get("error", {}).get("code", "")
        logger.info(
            "api_request",
            extra={
                "request_id": request.api_request_id,
                "endpoint": endpoint,
                "method": request.method,
                "status": response.status_code,
                "duration_seconds": round(monotonic() - started, 6),
                "error_code": error_code,
                **planning_activity(),
            },
        )
        return response

    def _guard(self, request, endpoint):
        if endpoint == "unknown_api":
            return None
        allowed = "POST" if endpoint == "trips" else "GET"
        if request.method != allowed:
            response = api_error(
                "METHOD_NOT_ALLOWED",
                "HTTP method is not supported.",
                405,
                request.api_request_id,
            )
            response["Allow"] = allowed
            return response
        if endpoint in ("trips", "trip_result", "trip_map"):
            retry = caller_limiter(
                settings.FUEL_API_RATE_REQUESTS,
                settings.FUEL_API_RATE_WINDOW_SECONDS,
                settings.FUEL_API_RATE_MAX_CALLERS,
            ).check(str(request.META.get("REMOTE_ADDR", "unknown")))
            if retry:
                response = api_error(
                    "RATE_LIMITED",
                    "Too many requests. Try again later.",
                    429,
                    request.api_request_id,
                )
                response["Retry-After"] = str(retry)
                return response
        if endpoint == "trips":
            try:
                length = int(request.META.get("CONTENT_LENGTH") or 0)
                if length < 0:
                    raise ValueError("negative length")
                if length > BODY_LIMIT:
                    raise RequestDataTooBig()
                # Read at most limit + 1 even if length is missing or unreliable.
                body = request.read(BODY_LIMIT + 1)
                if len(body) > BODY_LIMIT:
                    raise RequestDataTooBig()
                request._body = body
            except RequestDataTooBig:
                return api_error(
                    "REQUEST_TOO_LARGE",
                    "Request body is too large.",
                    413,
                    request.api_request_id,
                )
            except (BadRequest, ValueError, OSError):
                return api_error(
                    "INVALID_REQUEST",
                    "Request body is invalid.",
                    400,
                    request.api_request_id,
                )
        return None

    def process_exception(self, request, exception):
        if not hasattr(request, "api_request_id"):
            return None
        logger.error(
            "api_failure",
            extra={
                "request_id": request.api_request_id,
                "exception_type": type(exception).__name__,
            },
        )
        if isinstance(exception, DatabaseError):
            return api_error(
                "STORAGE_UNAVAILABLE",
                "Result storage is temporarily unavailable.",
                503,
                request.api_request_id,
            )
        return api_error(
            "INTERNAL_ERROR",
            "The request could not be completed.",
            500,
            request.api_request_id,
        )
