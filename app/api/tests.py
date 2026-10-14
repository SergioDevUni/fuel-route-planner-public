"""HTTP tests that exercise safe public envelopes and opaque result access."""

import json
import sqlite3
from concurrent.futures import Future
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from app.api.models import TripResult
from app.api.policy import check_final_plan_policy
from app.api.routing_config import national_local_config, partial_road_config
from app.api.runtime import close_after_futures
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget
from app.planning.trip_types import DurationLimitExceeded


class TripApiTests(TestCase):
    def test_result_write_queue_respects_finalization_deadline(self):
        from threading import Lock

        from app.api.persistence import persist_result, prepare_result

        writer = Lock()
        writer.acquire()
        saved, _, _ = prepare_result({"status": "verified"})
        before = TripResult.objects.count()
        try:
            with patch("app.api.persistence._result_writer", writer):
                with self.assertRaises(PlanningDeadlineExceeded):
                    persist_result(saved, RequestBudget(deadline_seconds=0.15))
        finally:
            writer.release()
        self.assertEqual(TripResult.objects.count(), before)

    def test_cache_completed_during_admission_race_bypasses_full_pool(self):
        from threading import BoundedSemaphore

        from app.api.cache import ResultCache

        cache = ResultCache()
        body = {
            "start": {"type": "coordinates", "latitude": 41, "longitude": -87},
            "finish": {"type": "coordinates", "latitude": 42, "longitude": -88},
        }
        with (
            patch("app.api.views.calculation_keys", return_value=("key", "f", "csv")),
            patch("app.api.views.trip_cache", cache),
            patch.object(
                cache, "get_completed", side_effect=[None, {"status": "verified"}]
            ),
            patch("app.api.deadline._planning_slots", BoundedSemaphore(0)),
            patch("app.api.views.calculate") as calculate,
        ):
            response = self.client.post(
                "/api/v1/trips", data=json.dumps(body), content_type="application/json"
            )
        self.assertEqual(response.status_code, 200)
        calculate.assert_not_called()

    def test_completed_cache_bypasses_full_pool_and_issues_fresh_credentials(self):
        from threading import BoundedSemaphore

        from app.api.cache import ResultCache

        cache = ResultCache()
        cache.put_completed("key", {"status": "verified", "fuel_stops": []})
        body = {
            "start": {"type": "coordinates", "latitude": 41, "longitude": -87},
            "finish": {"type": "coordinates", "latitude": 42, "longitude": -88},
        }
        with (
            patch(
                "app.api.views.calculation_keys",
                return_value=("key", "feasible", "csv"),
            ),
            patch("app.api.views.trip_cache", cache),
            patch("app.api.deadline._planning_slots", BoundedSemaphore(0)),
            patch("app.api.views.calculate") as calculate,
        ):
            responses = [
                self.client.post(
                    "/api/v1/trips",
                    data=json.dumps(body),
                    content_type="application/json",
                )
                for _ in range(2)
            ]
        self.assertEqual([r.status_code for r in responses], [200, 200])
        first, second = [r.json() for r in responses]
        self.assertNotEqual(first["trip_id"], second["trip_id"])
        self.assertNotEqual(first["result_token"], second["result_token"])
        calculate.assert_not_called()
        self.assertNotIn("trip_id", cache.get_completed("key"))
        for payload in (first, second):
            saved = self.client.get(
                f"/api/v1/trips/{payload['trip_id']}",
                HTTP_AUTHORIZATION="Bearer " + payload["result_token"],
            )
            self.assertEqual(saved.status_code, 200)

    def test_configured_native_transport_fallback_has_one_coherent_profile(self):
        from app.api.execution import calculate_uncached
        from app.api.validation import TripInput
        from app.domain.location import ResolvedLocation
        from app.routing.client import RoutingTransportUnavailable
        from tests.support.planning import FINISH, START, FakeCandidates, _route

        class Primary:
            def route(self, points, budget):
                budget.consume_attempt()
                raise RoutingTransportUnavailable("hosted transport unavailable")

            def close(self):
                pass

        class Native:
            closed = False

            def route(self, points, budget):
                budget.check_deadline()
                return _route(points, (1100,) if len(points) == 2 else (450, 450, 200))

            def close(self):
                self.closed = True

        native = Native()
        trip = TripInput(
            {},
            {},
            Decimal(50),
            Decimal(3),
            Decimal(0),
            None,
            None,
            "balanced",
            Decimal(30),
        )
        values = {
            "ORS_API_KEY": "test-key",
            "FUEL_MATRIX_GRAPH": "0",
            "FUEL_MATRIX_CACHE_VERSION": "native-fallback-test",
            "FUEL_NATIONAL_LOCAL_ROUTER": "0",
            "FUEL_NATIONAL_VALHALLA_URL": "http://local.test",
            "FUEL_NATIONAL_VALHALLA_VERSION": "road-test",
        }
        with (
            patch.dict("os.environ", values),
            patch("app.api.runtime.DatasetVersion.objects") as objects,
            patch("app.api.execution.load_candidates", return_value=FakeCandidates()),
            patch("app.api.runtime.national_local_config", return_value=None),
            patch("app.api.runtime.partial_road_config", return_value=None),
            patch("app.api.runtime.OpenRouteServiceAdapter", return_value=Primary()),
            patch("app.api.execution.LocalValhalla", return_value=native),
            patch("app.api.execution.record_selected_locality_stops"),
        ):
            budget = RequestBudget()
            objects.filter.return_value.first.return_value = SimpleNamespace(
                source_sha256="csv"
            )
            payload = calculate_uncached(
                trip,
                "test",
                ResolvedLocation(START, "coordinates", "IL"),
                ResolvedLocation(FINISH, "coordinates", "IL"),
                budget,
            )
        self.assertEqual(payload["assumptions"]["station_selection"], "fuel_cost")
        self.assertEqual(payload["costs"]["fuel_purchases_cost"], "195.00000000")
        self.assertEqual(budget.attempts, 1)
        self.assertTrue(native.closed)

    def test_final_policy_accepts_limit_and_rejects_one_second_more(self) -> None:
        baseline = SimpleNamespace(duration_seconds=Decimal(1000))
        verified = SimpleNamespace(duration_seconds=Decimal(2800))
        plan = SimpleNamespace(baseline=baseline, verified=verified)
        check_final_plan_policy(plan, Decimal(30), RequestBudget())
        verified.duration_seconds += 1
        with self.assertRaises(DurationLimitExceeded):
            check_final_plan_policy(plan, Decimal(30), RequestBudget())

    def test_client_cleanup_waits_for_all_pending_workers(self) -> None:
        first = Future()
        second = Future()
        closed: list[bool] = []
        close_after_futures((first, second), lambda: closed.append(True))
        first.set_result(None)
        self.assertFalse(closed)
        second.set_result(None)
        self.assertEqual(closed, [True])

    def test_partial_road_refiner_tracks_completed_sources(self) -> None:
        with TemporaryDirectory() as folder:
            database = Path(folder) / "var/road_nationwide/station_edges.db"
            database.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.executescript(
                    "CREATE TABLE metadata(key TEXT, value TEXT);"
                    "CREATE TABLE complete_source(point_id INTEGER PRIMARY KEY);"
                    "INSERT INTO metadata VALUES ('candidate_hash','csv-v1');"
                    "INSERT INTO metadata VALUES ('road_graph_version','road-v1');"
                    "INSERT INTO metadata VALUES "
                    "('schema','csv-station-edges-v1-auto');"
                )
            values = {
                "FUEL_PARTIAL_ROAD_REFINER": "1",
                "FUEL_NATIONAL_VALHALLA_URL": "http://127.0.0.1:8004",
                "FUEL_NATIONAL_VALHALLA_VERSION": "road-v1",
            }
            with patch.dict("os.environ", values), override_settings(BASE_DIR=folder):
                self.assertIsNone(partial_road_config("csv-v1"))
                with closing(sqlite3.connect(database)) as connection, connection:
                    connection.execute("INSERT INTO complete_source VALUES (7)")
                self.assertEqual(
                    partial_road_config("csv-v1"),
                    ("http://127.0.0.1:8004", "road-v1", database, 1),
                )
                self.assertIsNone(partial_road_config("other"))

    def test_local_routing_requires_a_matching_completion_marker(self) -> None:
        with TemporaryDirectory() as folder:
            database = Path(folder) / "var/road_nationwide/station_edges.db"
            marker = database.with_suffix(".complete.json")
            database.parent.mkdir(parents=True)
            database.touch()
            values = {
                "FUEL_NATIONAL_VALHALLA_URL": "http://127.0.0.1:8004",
                "FUEL_NATIONAL_VALHALLA_VERSION": "road-v1",
            }
            with patch.dict("os.environ", values), override_settings(BASE_DIR=folder):
                self.assertIsNone(national_local_config("csv-v1"))
                marker.write_text(
                    json.dumps(
                        {"graph_version": "road-v1", "candidate_version": "other"}
                    )
                )
                self.assertIsNone(national_local_config("csv-v1"))
                marker.write_text(
                    json.dumps(
                        {"graph_version": "road-v1", "candidate_version": "csv-v1"}
                    )
                )
                self.assertEqual(
                    national_local_config("csv-v1"),
                    ("http://127.0.0.1:8004", "road-v1", database),
                )

    def test_invalid_request_does_not_expose_internal_details(self) -> None:
        response = self.client.post(
            "/api/v1/trips",
            data=json.dumps({"start": "Chicago"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")
        self.assertNotIn("details", response.json()["error"])

    @patch(
        "app.api.views.run_with_deadline",
        side_effect=PlanningDeadlineExceeded("deadline"),
    )
    def test_outer_deadline_returns_timeout_without_saving(self, run) -> None:
        before = TripResult.objects.count()
        body = {
            "start": {"type": "coordinates", "latitude": 41.0, "longitude": -87.0},
            "finish": {"type": "coordinates", "latitude": 42.0, "longitude": -88.0},
        }
        response = self.client.post(
            "/api/v1/trips", data=json.dumps(body), content_type="application/json"
        )
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"]["code"], "PLANNING_TIMEOUT")
        self.assertEqual(TripResult.objects.count(), before)

    def test_saturation_is_busy_without_saving_a_result(self) -> None:
        from app.api.deadline import PlanningCapacityExceeded

        before = TripResult.objects.count()
        with patch(
            "app.api.views.run_with_deadline",
            side_effect=PlanningCapacityExceeded("full"),
        ):
            response = self.client.post(
                "/api/v1/trips",
                data=json.dumps(
                    {
                        "start": {"type": "city", "city": "Chicago", "state": "IL"},
                        "finish": {"type": "city", "city": "Miami", "state": "FL"},
                    }
                ),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "PLANNING_BUSY")
        self.assertEqual(response["Retry-After"], "1")
        self.assertEqual(TripResult.objects.count(), before)

    @patch("app.api.views.calculate", side_effect=ValueError("corrupt candidate hash"))
    def test_invalid_prepared_data_is_service_failure(self, calculate) -> None:
        body = {
            "start": {"type": "coordinates", "latitude": 41.0, "longitude": -87.0},
            "finish": {"type": "coordinates", "latitude": 42.0, "longitude": -88.0},
        }
        response = self.client.post(
            "/api/v1/trips", data=json.dumps(body), content_type="application/json"
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "DATASET_NOT_READY")
        self.assertNotIn("corrupt candidate hash", response.content.decode())

    def test_native_transport_failure_is_routing_failure(self) -> None:
        from app.routing.local_valhalla import LocalValhallaUnavailable

        with patch(
            "app.api.views.calculate",
            side_effect=LocalValhallaUnavailable("native transport failed"),
        ):
            response = self.client.post(
                "/api/v1/trips",
                data=json.dumps(
                    {
                        "start": {
                            "type": "coordinates",
                            "latitude": 41,
                            "longitude": -87,
                        },
                        "finish": {
                            "type": "coordinates",
                            "latitude": 42,
                            "longitude": -88,
                        },
                    }
                ),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "ROUTING_UNAVAILABLE")
        self.assertNotIn("native transport failed", response.content.decode())

    @patch(
        "app.api.views.calculate", return_value={"status": "verified", "fuel_stops": []}
    )
    def test_saved_result_requires_opaque_token(self, calculate) -> None:
        body = {
            "start": {"type": "coordinates", "latitude": 41.0, "longitude": -87.0},
            "finish": {"type": "coordinates", "latitude": 42.0, "longitude": -88.0},
        }
        response = self.client.post(
            "/api/v1/trips", data=json.dumps(body), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(
            result["map_url"],
            f"/maps/{result['trip_id']}#token={result['result_token']}",
        )
        url = f"/api/v1/trips/{result['trip_id']}"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.get(
                url, HTTP_AUTHORIZATION="Bearer invalid-token-123456"
            ).status_code,
            404,
        )
        fetched = self.client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {result['result_token']}"
        )
        self.assertEqual(fetched.status_code, 200)
        self.assertNotIn("result_token", fetched.json())
        self.assertEqual(fetched.json()["status"], "verified")

    def test_map_shell_has_no_result_data_or_token_in_html(self) -> None:
        response = self.client.get("/maps/example-result")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-trip-id="example-result"')
        self.assertContains(response, "Route details")
        self.assertContains(response, "Fuel purchases during trip")
        self.assertContains(response, "Estimated value of fuel consumed")
        self.assertNotContains(response, "token=", status_code=200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("script-src 'self'", response["Content-Security-Policy"])
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")
