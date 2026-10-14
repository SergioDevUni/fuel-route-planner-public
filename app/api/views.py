"""Create trips, retrieve private results and serve application status."""

import hashlib
import hmac
import logging
import uuid

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from app.api.cache import trip_cache
from app.api.coalescing import request_coalescer
from app.api.deadline import run_with_deadline
from app.api.errors import api_error
from app.api.failures import TRIP_FAILURES, trip_failure
from app.api.models import TripResult
from app.api.persistence import save_response
from app.api.readiness import application_ready
from app.api.request_execution import execute_request
from app.api.serialization import public_trip_payload
from app.api.service import calculate, calculation_keys
from app.api.validation import parse_trip_request
from app.planning.budget import RequestBudget

logger = logging.getLogger("app.api.operations")


def _error(code: str, message: str, status: int, request_id: str) -> JsonResponse:
    return api_error(code, message, status, request_id)


@csrf_exempt
@require_http_methods(["POST"])
def trips(request: HttpRequest) -> HttpResponse:
    budget = RequestBudget()
    request_id = getattr(request, "api_request_id", None) or uuid.uuid4().hex
    if not request.content_type == "application/json":
        return _error("UNSUPPORTED_MEDIA_TYPE", "JSON is required.", 415, request_id)
    if len(request.body) > 16 * 1024:
        return _error(
            "REQUEST_TOO_LARGE", "Request body is too large.", 413, request_id
        )
    try:
        trip = parse_trip_request(request.body)
    except ValueError:
        logger.info("invalid trip request", extra={"request_id": request_id})
        return _error("INVALID_REQUEST", "Trip request is invalid.", 400, request_id)
    try:
        return execute_request(
            trip,
            budget,
            calculation_keys=calculation_keys,
            trip_cache=trip_cache,
            calculate=calculate,
            request_coalescer=request_coalescer,
            run_with_deadline=run_with_deadline,
            save_response=save_response,
        )
    except TRIP_FAILURES as error:
        return trip_failure(error, request_id)


@require_http_methods(["GET"])
def trip_result(request: HttpRequest, trip_id: str) -> JsonResponse:
    request_id = getattr(request, "api_request_id", None) or uuid.uuid4().hex
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return _error("RESULT_NOT_FOUND", "Result was not found.", 404, request_id)
    token = header[7:]
    if (
        not 20 <= len(token) <= 128
        or not token.isascii()
        or not all(char.isalnum() or char in "_-" for char in token)
    ):
        return _error("RESULT_NOT_FOUND", "Result was not found.", 404, request_id)
    saved = TripResult.objects.filter(public_id=trip_id).first()
    if saved is None or not hmac.compare_digest(
        saved.token_hash, hashlib.sha256(token.encode()).hexdigest()
    ):
        return _error("RESULT_NOT_FOUND", "Result was not found.", 404, request_id)
    if saved.expires_at <= timezone.now():
        return _error("RESULT_EXPIRED", "Result has expired.", 410, request_id)
    response = JsonResponse(public_trip_payload(saved.payload))
    response["Cache-Control"] = "no-store"
    return response


@require_http_methods(["GET"])
def trip_map(request: HttpRequest, trip_id: str) -> HttpResponse:
    response = render(request, "app/trip_map.html", {"trip_id": trip_id})
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://tile.openstreetmap.org; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


def live(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "live"})


def ready(request: HttpRequest) -> JsonResponse:
    available = application_ready()
    return JsonResponse(
        {"status": "ready" if available else "not_ready"},
        status=200 if available else 503,
    )
