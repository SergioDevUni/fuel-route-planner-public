"""Private saved-result identifiers, expiry and token storage."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import timedelta

from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import TextField, Value
from django.http import HttpResponse
from django.utils import timezone

from app.api.models import TripResult
from app.api.result_writer import ResultWriter
from app.api.serialization import public_trip_payload
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget

logger = logging.getLogger("app.api.service")
_result_writer = ResultWriter()


def prepare_result(payload: dict) -> tuple[TripResult, dict, str]:
    public_id = secrets.token_urlsafe(18)
    token = secrets.token_urlsafe(32)
    expiry = timezone.now() + timedelta(hours=24)
    stored = {
        **public_trip_payload(payload),
        "trip_id": public_id,
        "expires_at": expiry.isoformat(),
    }
    saved = TripResult(
        public_id=public_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        payload=stored,
        expires_at=expiry,
    )
    return (
        saved,
        {
            **stored,
            "map_url": f"/maps/{public_id}#token={token}",
        },
        token,
    )


def persist_result(saved: TripResult, budget: RequestBudget | None = None) -> None:
    """Queue process-local writes before contending for SQLite's single writer."""
    if budget is None:
        encoded = json.dumps(
            saved.payload, cls=DjangoJSONEncoder, separators=(",", ":")
        )
        with _result_writer:
            _persist_result_locked(saved, None, encoded)
        return
    budget.check_finalization_deadline(0.1)
    with budget.stage("result_payload_encoding"):
        encoded = json.dumps(
            saved.payload, cls=DjangoJSONEncoder, separators=(",", ":")
        )
    budget.check_finalization_deadline(0.1)
    with budget.stage("result_writer_wait"):
        acquired = _result_writer.acquire(
            timeout=max(0, budget.remaining_seconds - 0.1)
        )
    if not acquired:
        raise PlanningDeadlineExceeded("result persistence queue exceeded deadline")
    try:
        _persist_result_locked(saved, budget, encoded)
    finally:
        _result_writer.release()


def _insert_prepared(saved: TripResult, encoded: str) -> None:
    """Bind pre-encoded JSON as text; ORM insertion and field hooks stay intact."""
    payload = saved.payload
    saved.payload = Value(encoded, output_field=TextField())
    try:
        saved.save(force_insert=True)
    finally:
        saved.payload = payload


def _persist_result_locked(
    saved: TripResult, budget: RequestBudget | None, encoded: str
) -> None:
    """Bound SQLite lock waits and roll back if time expires before commit."""
    if budget is None:
        _insert_prepared(saved, encoded)
        return
    budget.check_finalization_deadline(0.1)
    with budget.stage("result_connection"):
        connection.ensure_connection()
    with connection.cursor() as cursor:
        previous_timeout = cursor.execute("PRAGMA busy_timeout").fetchone()[0]
        # BEGIN and COMMIT can each contend. Divide the remaining allowance,
        # retaining time for middleware and HTTP scheduling after commit.
        timeout_ms = min(250, int((budget.remaining_seconds - 0.1) * 1000 / 3))
        cursor.execute(f"PRAGMA busy_timeout = {max(0, timeout_ms)}")
    try:
        with budget.stage("result_persistence"), transaction.atomic():
            budget.check_finalization_deadline(0.1)
            _insert_prepared(saved, encoded)
            budget.check_finalization_deadline(0.1)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"PRAGMA busy_timeout = {previous_timeout}")


def save_result(payload: dict) -> tuple[dict, str]:
    saved, stored, token = prepare_result(payload)
    persist_result(saved)
    return stored, token


def save_response(payload: dict, budget: RequestBudget) -> HttpResponse:
    """Encode before committing so successful persistence needs no large encoding."""
    budget.check_finalization_deadline(0.1)
    with budget.stage("response_encoding"):
        saved, stored, token = prepare_result(payload)
        response = HttpResponse(
            json.dumps(
                {**stored, "result_token": token},
                cls=DjangoJSONEncoder,
                separators=(",", ":"),
            ),
            content_type="application/json",
        )
    persist_result(saved, budget)
    return response
