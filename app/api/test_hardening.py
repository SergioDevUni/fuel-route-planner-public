"""HTTP and persistent-result acceptance tests, without hosted provider calls."""

import hashlib
import json
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import yaml
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from app.api.middleware import ApiBoundaryMiddleware, caller_limiter
from app.api.models import TripResult
from app.domain.location import ResolvedLocation
from app.domain.routing import Coordinates

POINT = {"type": "coordinates", "latitude": 41, "longitude": -87}
BODY = {"start": POINT, "finish": POINT}


class ApiHardeningTests(TestCase):
    def setUp(self):
        caller_limiter.cache_clear()

    def post(self, body=BODY):
        return self.client.post(
            "/api/v1/trips", json.dumps(body), content_type="application/json"
        )

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertEqual(
            response["X-Request-ID"], response.json()["error"]["request_id"]
        )
        self.assertEqual(response["Cache-Control"], "no-store")
        spec = yaml.safe_load(
            (Path(__file__).resolve().parents[2] / "docs/openapi.yaml").read_text()
        )
        Draft202012Validator(spec["components"]["schemas"]["ErrorEnvelope"]).validate(
            response.json()
        )

    def test_malformed_input_never_escapes_as_server_failure(self):
        for value in ([], {}, None, 5):
            with self.subTest(value=value):
                self.assert_error(
                    self.post({**BODY, "start": {"type": value}}),
                    400,
                    "INVALID_REQUEST",
                )
        self.assert_error(
            self.post({**BODY, "start": {**POINT, "latitude": 10**400}}),
            400,
            "INVALID_REQUEST",
        )
        self.assert_error(
            self.post({**BODY, "initial_fuel_price_per_gallon": "1e100000"}),
            400,
            "INVALID_REQUEST",
        )
        self.assert_error(
            self.client.post(
                "/api/v1/trips",
                "[" * 1100 + "]" * 1100,
                content_type="application/json",
            ),
            400,
            "INVALID_REQUEST",
        )

    def test_oversized_and_wrong_content_type_start_no_planning(self):
        with patch("app.api.views.calculate") as calculate:
            self.assert_error(
                self.client.post(
                    "/api/v1/trips", b"x" * 16385, content_type="application/json"
                ),
                413,
                "REQUEST_TOO_LARGE",
            )
            self.assert_error(
                self.client.post("/api/v1/trips", b"{}", content_type="text/plain"),
                415,
                "UNSUPPORTED_MEDIA_TYPE",
            )
            calculate.assert_not_called()

    def test_bounded_reader_does_not_use_unbounded_body_property(self):
        request = RequestFactory().post(
            "/api/v1/trips", b"x" * 16385, content_type="application/json"
        )
        request.META["CONTENT_LENGTH"] = "0"
        with patch.object(request, "read", wraps=request.read) as read:
            response = ApiBoundaryMiddleware(lambda r: self.fail("body accepted"))(
                request
            )
        self.assertEqual(response.status_code, 413)
        read.assert_called_once_with(16385)

    def test_methods_and_unknown_api_use_json_errors(self):
        self.assert_error(self.client.get("/api/v1/trips"), 405, "METHOD_NOT_ALLOWED")
        self.assert_error(
            self.client.post("/api/v1/trips/example"), 405, "METHOD_NOT_ALLOWED"
        )
        self.assert_error(self.client.post("/health/live"), 405, "METHOD_NOT_ALLOWED")
        self.assert_error(self.client.get("/api/v1/unknown"), 404, "NOT_FOUND")

    @override_settings(FUEL_API_RATE_REQUESTS=2)
    def test_rate_limit_is_separate_from_admission_and_ignores_spoofed_ip(self):
        with patch("app.api.views.calculate") as calculate:
            for _ in range(2):
                self.assertEqual(self.post({}).status_code, 400)
            response = self.client.post(
                "/api/v1/trips",
                json.dumps(BODY),
                content_type="application/json",
                HTTP_X_FORWARDED_FOR="203.0.113.4",
            )
            self.assert_error(response, 429, "RATE_LIMITED")
            self.assertGreater(int(response["Retry-After"]), 0)
            calculate.assert_not_called()
        self.assertEqual(self.client.get("/health/live").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/v1/trips",
                "{}",
                content_type="application/json",
                REMOTE_ADDR="203.0.113.5",
            ).status_code,
            400,
        )

    def test_database_errors_and_unexpected_errors_are_safe(self):
        for error, status, code in (
            (OperationalError("private db path"), 503, "STORAGE_UNAVAILABLE"),
            (RuntimeError("secret-key"), 500, "INTERNAL_ERROR"),
        ):
            with (
                self.subTest(code=code),
                patch("app.api.views.save_response", side_effect=error),
                patch("app.api.views.calculate", return_value={}),
            ):
                self.assert_error(self.post(), status, code)
        with patch(
            "app.api.views.TripResult.objects.filter",
            side_effect=OperationalError("private database"),
        ):
            self.assert_error(
                self.client.get(
                    "/api/v1/trips/example", HTTP_AUTHORIZATION="Bearer " + "a" * 32
                ),
                503,
                "STORAGE_UNAVAILABLE",
            )

    def test_expired_correct_token_returns_410_wrong_token_returns_404(self):
        token = "a" * 32
        TripResult.objects.create(
            public_id="expired",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            payload={},
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        url = "/api/v1/trips/expired"
        self.assert_error(
            self.client.get(url, HTTP_AUTHORIZATION="Bearer " + token),
            410,
            "RESULT_EXPIRED",
        )
        self.assert_error(
            self.client.get(url, HTTP_AUTHORIZATION="Bearer " + "b" * 32),
            404,
            "RESULT_NOT_FOUND",
        )

    def test_real_zero_trip_creation_and_retrieval_match_openapi(self):
        place = ResolvedLocation(Coordinates(41, -87), "coordinates", "IL")
        with patch("app.api.service.resolve_inputs", return_value=(place, place)):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        spec = yaml.safe_load(
            (Path(__file__).resolve().parents[2] / "docs/openapi.yaml").read_text()
        )
        spec["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        uri = "urn:fuel-route:openapi"
        registry = Registry().with_resource(uri, Resource.from_contents(spec))
        for schema, result in (
            ("CreatedTrip", response),
            (
                "SavedTrip",
                self.client.get(
                    "/api/v1/trips/" + response.json()["trip_id"],
                    HTTP_AUTHORIZATION="Bearer " + response.json()["result_token"],
                ),
            ),
        ):
            Draft202012Validator(
                {"$ref": uri + "#/components/schemas/" + schema}, registry=registry
            ).validate(result.json())
            self.assertEqual(result["Cache-Control"], "no-store")
        self.assertEqual(response.json()["status"], "zero_distance")

    def test_cleanup_is_bounded_and_preserves_unexpired_results(self):
        now = timezone.now()
        for index in range(4):
            TripResult.objects.create(
                public_id=str(index),
                token_hash="a" * 64,
                payload={},
                expires_at=now + timedelta(hours=1)
                if index == 3
                else now - timedelta(hours=1),
            )
        output = StringIO()
        call_command("purge_expired_trips", dry_run=True, stdout=output)
        self.assertEqual(TripResult.objects.count(), 4)
        call_command("purge_expired_trips", batch_size=1, max_batches=2, stdout=output)
        self.assertEqual(TripResult.objects.count(), 2)
        self.assertTrue(TripResult.objects.filter(public_id="3").exists())
        call_command("purge_expired_trips", stdout=output)
        self.assertEqual(TripResult.objects.count(), 1)
        with self.assertRaises(CommandError):
            call_command("purge_expired_trips", batch_size=0)
