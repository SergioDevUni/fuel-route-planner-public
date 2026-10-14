"""Adversarial HTTP tests and real planner-to-persistence requirement checks."""

import hashlib
import json
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import yaml
from django.db import OperationalError
from django.test import (
    Client,
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from app.api.middleware import ApiBoundaryMiddleware, caller_limiter
from app.api.models import TripResult
from app.api.observability import OperationalFormatter
from app.api.persistence import save_result
from app.api.policy import check_final_plan_policy
from app.api.serialization import serialize_trip
from app.domain.location import (
    LocationAmbiguous,
    LocationNotFound,
    LocationOutsideUS,
    ResolvedLocation,
)
from app.planning.budget import PlanningDeadlineExceeded, RoutingBudgetExceeded
from app.planning.ledger import InitialFuelPriceRequired
from app.planning.trip import TripPlanner
from app.planning.trip_types import (
    DetourLimitExceeded,
    DurationLimitExceeded,
    NoFeasiblePlan,
    RouteCheckFailed,
)
from app.routing.client import (
    NoDrivableRoute,
    RoutingAuthenticationFailed,
    RoutingLimitExceeded,
    RoutingRequestRejected,
    RoutingUnavailable,
)
from app.routing.local_valhalla import LocalValhallaUnavailable
from app.routing.normalizer import RoutingResponseInvalid
from tests.support.planning import FINISH, START, STATIONS, FakeCandidates, FakeRouter

BODY = {
    "start": {"type": "coordinates", "latitude": 40, "longitude": -90},
    "finish": {"type": "coordinates", "latitude": 40, "longitude": -70},
    "initial_fuel_price_per_gallon": "3.20",
}


class BoundaryLoggingTests(SimpleTestCase):
    def test_success_keeps_correlation_without_parsing_response_json(self):
        from django.http import JsonResponse

        middleware = ApiBoundaryMiddleware(lambda request: JsonResponse({"ok": True}))
        request = RequestFactory().get("/health/live")
        with (
            patch("app.api.middleware.json.loads", side_effect=AssertionError),
            patch("app.api.middleware.logger.info") as log,
        ):
            response = middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Request-ID"], request.api_request_id)
        self.assertEqual(log.call_args.kwargs["extra"]["error_code"], "")

    def test_error_still_logs_safe_code(self):
        from django.http import JsonResponse

        middleware = ApiBoundaryMiddleware(
            lambda request: JsonResponse({"error": {"code": "FAILED"}}, status=503)
        )
        with patch("app.api.middleware.logger.info") as log:
            response = middleware(RequestFactory().get("/health/live"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(log.call_args.kwargs["extra"]["error_code"], "FAILED")


def planned_payload(trip, budget):
    result = TripPlanner(FakeRouter(), FakeCandidates()).plan(
        START,
        FINISH,
        trip.initial_fuel_gallons,
        trip.reserve_gallons,
        trip.max_detour_miles,
        trip.max_detour_ratio,
        trip.initial_fuel_price_per_gallon,
        trip.optimization_mode,
        trip.max_extra_duration_minutes,
        budget=budget,
    )
    check_final_plan_policy(result, trip.max_extra_duration_minutes, budget)
    return serialize_trip(
        trip,
        ResolvedLocation(START, "coordinates", "IL"),
        ResolvedLocation(FINISH, "coordinates", "PA"),
        result,
    )


class ApiRequirementsTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        spec = yaml.safe_load(
            (Path(__file__).resolve().parents[2] / "docs/openapi.yaml").read_text()
        )
        spec["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        uri = "urn:fuel-route:openapi"
        registry = Registry().with_resource(uri, Resource.from_contents(spec))
        cls.created_schema = Draft202012Validator(
            {"$ref": uri + "#/components/schemas/CreatedTrip"}, registry=registry
        )
        cls.saved_schema = Draft202012Validator(
            {"$ref": uri + "#/components/schemas/SavedTrip"}, registry=registry
        )

    def setUp(self):
        caller_limiter.cache_clear()

    def post(self, body=BODY, **headers):
        return self.client.post(
            "/api/v1/trips",
            json.dumps(body),
            content_type="application/json",
            **headers,
        )

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertEqual(
            response["X-Request-ID"], response.json()["error"]["request_id"]
        )
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_exact_body_limit_is_accepted_and_next_byte_is_rejected(self):
        body = json.dumps(BODY).encode()
        with patch("app.api.views.calculate", side_effect=LocationNotFound):
            self.assert_error(
                self.client.post(
                    "/api/v1/trips",
                    body.ljust(16384, b" "),
                    content_type="application/json",
                ),
                422,
                "LOCATION_NOT_FOUND",
            )
        with patch("app.api.views.calculate") as calculate:
            self.assert_error(
                self.client.post(
                    "/api/v1/trips",
                    body.ljust(16385, b" "),
                    content_type="application/json",
                ),
                413,
                "REQUEST_TOO_LARGE",
            )
            calculate.assert_not_called()

    def test_bad_encoding_duplicate_keys_and_control_text_never_start_work(self):
        bodies = (
            b"\xff",
            b" ",
            b'{"start":null,"start":{}}',
            json.dumps(
                {**BODY, "start": {"type": "city", "city": "x\x00", "state": "IL"}}
            ).encode(),
            json.dumps(
                {**BODY, "start": {"type": "city", "city": "x\ud800", "state": "IL"}}
            ).encode(),
        )
        with patch("app.api.views.calculate") as calculate:
            for body in bodies:
                with self.subTest(body=body):
                    self.assert_error(
                        self.client.post(
                            "/api/v1/trips", body, content_type="application/json"
                        ),
                        400,
                        "INVALID_REQUEST",
                    )
            calculate.assert_not_called()

    def test_failure_matrix_returns_safe_correlated_errors_and_saves_nothing(self):
        failures = (
            (LocationAmbiguous((), False), 422, "LOCATION_AMBIGUOUS"),
            (LocationNotFound, 422, "LOCATION_NOT_FOUND"),
            (LocationOutsideUS, 422, "LOCATION_OUTSIDE_US"),
            (InitialFuelPriceRequired, 422, "INITIAL_FUEL_PRICE_REQUIRED"),
            (NoFeasiblePlan, 422, "TRIP_OPTIONS_REQUIRED"),
            (DetourLimitExceeded, 422, "DETOUR_LIMIT_EXCEEDED"),
            (DurationLimitExceeded, 422, "DURATION_LIMIT_EXCEEDED"),
            (RouteCheckFailed, 422, "ROUTE_CHECK_LIMIT_REACHED"),
            (NoDrivableRoute, 422, "NO_DRIVABLE_ROUTE"),
            (RoutingLimitExceeded, 422, "ROUTE_LIMIT_EXCEEDED"),
            (RoutingRequestRejected, 422, "NO_DRIVABLE_ROUTE"),
            (RoutingBudgetExceeded, 422, "ROUTE_CHECK_LIMIT_REACHED"),
            (PlanningDeadlineExceeded, 504, "PLANNING_TIMEOUT"),
            (RoutingUnavailable, 503, "ROUTING_UNAVAILABLE"),
            (RoutingAuthenticationFailed, 503, "ROUTING_UNAVAILABLE"),
            (LocalValhallaUnavailable, 503, "ROUTING_UNAVAILABLE"),
            (RoutingResponseInvalid, 502, "ROUTING_RESPONSE_INVALID"),
            (FileNotFoundError, 503, "DATASET_NOT_READY"),
            (OSError, 503, "DATASET_NOT_READY"),
            (KeyError, 503, "DATASET_NOT_READY"),
            (ValueError, 503, "DATASET_NOT_READY"),
            (OperationalError, 503, "STORAGE_UNAVAILABLE"),
            (RuntimeError, 500, "INTERNAL_ERROR"),
        )
        for exception, status, code in failures:
            error = (
                exception("private-path-and-provider-secret")
                if isinstance(exception, type)
                else exception
            )
            with (
                self.subTest(code=code, exception=type(error).__name__),
                patch("app.api.views.calculate", side_effect=error),
            ):
                response = self.post()
                self.assert_error(response, status, code)
                self.assertNotIn(b"private-path-and-provider-secret", response.content)
        self.assertEqual(TripResult.objects.count(), 0)

    def test_trip_options_guidance_does_not_create_a_saved_trip(self):
        with patch("app.api.views.calculate", side_effect=NoFeasiblePlan):
            response = self.post()
        self.assert_error(response, 422, "TRIP_OPTIONS_REQUIRED")
        self.assertEqual(
            response.json()["error"]["message"],
            "Adjust your starting fuel or choose different endpoints.",
        )
        self.assertEqual(TripResult.objects.count(), 0)

    def test_saved_trip_presents_route_details_without_search_diagnostics(self):
        saved, token = save_result(
            {
                "status": "verified",
                "assumptions": {"road_legs_verified": True},
            }
        )
        record = TripResult.objects.get(public_id=saved["trip_id"])
        record.payload["assumptions"].update(
            {
                "station_selection": "local_road_recovery",
                "optimization_interrupted": True,
                "optimization_evidence": {"searches": []},
            }
        )
        record.save(update_fields=["payload"])
        response = self.client.get(
            "/api/v1/trips/" + saved["trip_id"],
            HTTP_AUTHORIZATION="Bearer " + token,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["assumptions"],
            {
                "road_legs_verified": True,
                "station_selection": "fuel_cost",
            },
        )
        record.refresh_from_db()
        self.assertIn("optimization_evidence", record.payload["assumptions"])

    def test_expiry_exact_boundary_and_cross_result_tokens(self):
        stored, token = save_result({"status": "test"})
        other, other_token = save_result({"status": "other"})
        saved = TripResult.objects.get(public_id=stored["trip_id"])
        url = "/api/v1/trips/" + stored["trip_id"]
        with patch(
            "app.api.views.timezone.now",
            return_value=saved.expires_at - timedelta(microseconds=1),
        ):
            self.assertEqual(
                self.client.get(url, HTTP_AUTHORIZATION="Bearer " + token).status_code,
                200,
            )
        with patch("app.api.views.timezone.now", return_value=saved.expires_at):
            self.assert_error(
                self.client.get(url, HTTP_AUTHORIZATION="Bearer " + token),
                410,
                "RESULT_EXPIRED",
            )
            self.assert_error(
                self.client.get(url, HTTP_AUTHORIZATION="Bearer " + other_token),
                404,
                "RESULT_NOT_FOUND",
            )
        self.assert_error(
            self.client.get(
                "/api/v1/trips/" + other["trip_id"],
                HTTP_AUTHORIZATION="Bearer " + token,
            ),
            404,
            "RESULT_NOT_FOUND",
        )

    def test_malformed_bearer_values_are_not_server_errors(self):
        stored, _ = save_result({})
        for header in (
            "",
            "bearer " + "a" * 32,
            "Bearer " + "a" * 129,
            "Bearer " + "\ud800" * 32,
            "Bearer " + "ÃƒÆ’Ã‚Â©" * 32,
            "Bearer " + "a a" * 10,
        ):
            with self.subTest(header=repr(header)):
                self.assert_error(
                    self.client.get(
                        "/api/v1/trips/" + stored["trip_id"], HTTP_AUTHORIZATION=header
                    ),
                    404,
                    "RESULT_NOT_FOUND",
                )

    def test_generated_token_hash_is_stored_without_plaintext_token(self):
        stored, token = save_result({"status": "test"})
        saved = TripResult.objects.get(public_id=stored["trip_id"])
        self.assertEqual(saved.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, json.dumps(saved.payload))
        self.assertNotIn("result_token", saved.payload)
        self.assertIn("#token=" + token, stored["map_url"])

    @override_settings(FUEL_API_RATE_REQUESTS=2)
    def test_submission_result_and_map_share_quota_but_health_does_not(self):
        self.assert_error(self.post({}), 400, "INVALID_REQUEST")
        self.assert_error(
            self.client.get("/api/v1/trips/missing"), 404, "RESULT_NOT_FOUND"
        )
        self.assert_error(self.client.get("/maps/missing"), 429, "RATE_LIMITED")
        self.assertEqual(self.client.get("/health/live").status_code, 200)

    def test_actual_operational_logs_exclude_body_token_query_and_spoofed_id(self):
        private = "private-canary-location-token"
        with self.assertLogs("app.api.operations", level="INFO") as captured:
            response = self.client.post(
                "/api/v1/trips?secret=" + private,
                json.dumps(
                    {
                        **BODY,
                        "start": {"type": "city", "city": private, "state": "IL"},
                        "unknown": private,
                    }
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + private,
                HTTP_X_REQUEST_ID=private,
            )
        output = "\n".join(
            OperationalFormatter().format(record) for record in captured.records
        )
        self.assertNotIn(private, output)
        self.assertIn(response["X-Request-ID"], output)
        self.assertNotEqual(response["X-Request-ID"], private)
        self.assertNotEqual(response["X-Request-ID"], self.post({})["X-Request-ID"])

    def test_stateless_json_submission_works_with_csrf_enforcement(self):
        with patch("app.api.views.calculate", side_effect=LocationNotFound):
            self.assert_error(
                Client(enforce_csrf_checks=True).post(
                    "/api/v1/trips", json.dumps(BODY), content_type="application/json"
                ),
                422,
                "LOCATION_NOT_FOUND",
            )

    def test_verified_deadline_fallback_is_saved_and_pending_check_work_is_not(self):
        published = []

        def interrupted(trip, budget):
            payload = planned_payload(trip, budget)
            published.append(payload)
            budget.publish_verified_payload(
                payload, (Decimal(160), Decimal(300), 2, ("1", "2"))
            )
            raise PlanningDeadlineExceeded("improvement exhausted time")

        with patch("app.api.views.calculate", side_effect=interrupted):
            response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        self.created_schema.validate(response.json())
        self.assertNotIn("optimization_interrupted", response.json()["assumptions"])
        self.assertNotIn("optimization_interrupted", published[0]["assumptions"])
        self.assertEqual(TripResult.objects.count(), 1)
        with patch("app.api.views.calculate", side_effect=PlanningDeadlineExceeded):
            self.assert_error(self.post(), 504, "PLANNING_TIMEOUT")
        self.assertEqual(TripResult.objects.count(), 1)

    def test_real_planner_serialization_and_saved_get_preserve_fuel_constraints(self):
        for initial, reserve in (("50", "0"), ("50", "5"), ("45", "5")):
            budgets = []

            def calculate(trip, budget, budgets=budgets):
                budgets.append(budget)
                return planned_payload(trip, budget)

            with (
                self.subTest(initial=initial, reserve=reserve),
                patch("app.api.views.calculate", side_effect=calculate),
            ):
                response = self.post(
                    {
                        **BODY,
                        "initial_fuel_gallons": initial,
                        "reserve_gallons": reserve,
                    }
                )
                self.assertEqual(response.status_code, 200, response.content)
                payload = response.json()
                self.created_schema.validate(payload)
                self.assertEqual(payload["status"], "verified")
                self.assertEqual(payload["vehicle"]["range_miles"], 500)
                self.assertEqual(payload["vehicle"]["miles_per_gallon"], 10)
                self.assertEqual(payload["vehicle"]["capacity_gallons"], 50)
                balance, purchased = Decimal(initial), Decimal(0)
                for leg in payload["route"]["legs"]:
                    arrival = Decimal(leg["arrival_fuel_gallons"])
                    departure = Decimal(leg["departure_fuel_gallons"])
                    self.assertEqual(
                        balance - Decimal(leg["fuel_consumed_gallons"]), arrival
                    )
                    self.assertEqual(
                        Decimal(leg["fuel_consumed_gallons"]),
                        Decimal(leg["distance_miles"]) / 10,
                    )
                    self.assertGreaterEqual(arrival, Decimal(reserve))
                    self.assertLessEqual(departure, 50)
                    self.assertGreaterEqual(departure, arrival)
                    purchased += departure - arrival
                    balance = departure
                costs = payload["costs"]
                self.assertEqual(balance, Decimal(costs["ending_fuel_gallons"]))
                self.assertEqual(purchased, Decimal(costs["fuel_purchased_gallons"]))
                self.assertEqual(
                    Decimal(initial) + purchased - balance,
                    Decimal(costs["fuel_consumed_gallons"]),
                )
                self.assertEqual(
                    sum(Decimal(s["purchase_cost"]) for s in payload["fuel_stops"]),
                    Decimal(costs["fuel_purchases_cost"]),
                )
                offers = {s.station_id: s.price_per_gallon for s in STATIONS}
                for stop in payload["fuel_stops"]:
                    self.assertEqual(
                        Decimal(stop["price_per_gallon"]), offers[stop["station_id"]]
                    )
                self.assertLessEqual(
                    Decimal(payload["route"]["extra_duration_seconds"]), 30 * 60
                )
                self.assertLessEqual(budgets[0].attempts, 4)
                self.assertTrue(payload["assumptions"]["road_legs_verified"])
                fetched = self.client.get(
                    "/api/v1/trips/" + payload["trip_id"],
                    HTTP_AUTHORIZATION="Bearer " + payload["result_token"],
                )
                self.assertEqual(fetched.status_code, 200)
                self.saved_schema.validate(fetched.json())
                for field in ("route", "vehicle", "fuel_stops", "costs", "assumptions"):
                    self.assertEqual(fetched.json()[field], payload[field])
                self.assertNotIn("result_token", fetched.json())

    def test_openapi_detects_nested_response_contract_regressions(self):
        with patch("app.api.views.calculate", side_effect=planned_payload):
            response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.created_schema.validate(payload)
        cases = (
            (("route",), "geometry", None),
            (("route", "geometry"), "type", "Point"),
            (("route", "geometry"), "coordinates", [[-181, 40], [-70, 40]]),
            (("route", "legs", 0), "arrival_fuel_gallons", None),
            (("route", "legs", 0), "departure_fuel_gallons", "-0.00000001"),
            (("fuel_stops", 0), "station_id", None),
            (("costs",), "currency", "CAD"),
            (("costs",), "fuel_consumed_gallons", None),
            (("vehicle",), "miles_per_gallon", 12),
            (("assumptions",), "road_legs_verified", False),
        )
        for path, field, value in cases:
            with self.subTest(path=path, field=field):
                broken = deepcopy(payload)
                target = broken
                for part in path:
                    target = target[part]
                if value is None:
                    target.pop(field)
                else:
                    target[field] = value
                with self.assertRaises(ValidationError):
                    self.created_schema.validate(broken)

    def test_zero_extra_time_cap_rejects_verified_but_slower_route(self):
        with patch("app.api.views.calculate", side_effect=planned_payload):
            self.assert_error(
                self.post({**BODY, "max_extra_duration_minutes": "0"}),
                422,
                "DURATION_LIMIT_EXCEEDED",
            )
        self.assertEqual(TripResult.objects.count(), 0)
