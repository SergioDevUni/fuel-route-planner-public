"""Commit and encoding boundaries retain the HTTP response deadline."""

from decimal import Decimal
from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase

from app.api.models import TripResult
from app.api.persistence import persist_result, prepare_result, save_response
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget


class PersistenceDeadlineTests(TestCase):
    def test_prepared_json_round_trips_and_saved_object_keeps_original_payload(self):
        payload = {
            "geometry": [[-110.17, 38.995]],
            "label": 'Quoted "雪"',
            "none": None,
        }
        saved, _, _ = prepare_result(payload)
        original = saved.payload
        persist_result(saved, RequestBudget())
        self.assertIs(saved.payload, original)
        self.assertEqual(TripResult.objects.get(pk=saved.pk).payload, original)

    def test_storage_failure_restores_prepared_object_payload(self):
        saved, _, _ = prepare_result({"status": "verified"})
        original = saved.payload
        with patch.object(TripResult, "save", side_effect=OperationalError("locked")):
            with self.assertRaises(OperationalError):
                persist_result(saved, RequestBudget())
        self.assertIs(saved.payload, original)
        self.assertEqual(TripResult.objects.count(), 0)

    def test_stopping_improvement_does_not_cancel_successful_finalization(self):
        budget = RequestBudget()
        budget.stop_work()
        response = save_response({"status": "verified"}, budget)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TripResult.objects.count(), 1)

    def test_expired_budget_saves_nothing(self):
        now = [0.0]
        budget = RequestBudget(deadline_seconds=1, clock=lambda: now[0])
        now[0] = 1
        with self.assertRaises(PlanningDeadlineExceeded):
            save_response({}, budget)
        self.assertEqual(TripResult.objects.count(), 0)

    def test_expiry_during_insert_rolls_back(self):
        now = [0.0]
        budget = RequestBudget(deadline_seconds=1, clock=lambda: now[0])
        original = TripResult.save

        def slow_insert(saved, *args, **kwargs):
            original(saved, *args, **kwargs)
            now[0] = 1

        with patch.object(TripResult, "save", slow_insert):
            with self.assertRaises(PlanningDeadlineExceeded):
                save_response({}, budget)
        self.assertEqual(TripResult.objects.count(), 0)

    def test_encoding_failure_happens_before_insert(self):
        with self.assertRaises(TypeError):
            save_response({"unsupported": object()}, RequestBudget())
        self.assertEqual(TripResult.objects.count(), 0)

    def test_storage_failure_does_not_report_success(self):
        with patch.object(TripResult, "save", side_effect=OperationalError("locked")):
            with self.assertRaises(OperationalError):
                save_response({"amount": Decimal("1.2")}, RequestBudget())
        self.assertEqual(TripResult.objects.count(), 0)
