"""Optional persistence attribution for the disposable cached HTTP benchmark."""

import json
from contextlib import ExitStack
from functools import wraps
from unittest.mock import patch


class CachedProfile:
    def __init__(self, context, *, enabled=True):
        self.context = context
        self.enabled = enabled
        self.stack = ExitStack()

    def _timed(self, original, name):
        @wraps(original)
        def call(*args, **kwargs):
            budget = getattr(self.context, "budget", None)
            if budget is None or not getattr(self.context, "saving", False):
                return original(*args, **kwargs)
            with budget.stage(name):
                return original(*args, **kwargs)

        return call

    def __enter__(self):
        if not self.enabled:
            return self
        from django.db.backends.base.base import BaseDatabaseWrapper
        from django.db.backends.sqlite3.operations import DatabaseOperations

        from app.api import persistence
        from app.api.models import TripResult

        original = persistence.save_response

        def save_response(payload, budget):
            self.context.saving = True
            try:
                response = original(payload, budget)
                response["X-Probe-Persistence-Stages"] = json.dumps(
                    budget.stage_seconds
                )
                return response
            finally:
                self.context.saving = False

        try:
            self.stack.enter_context(
                patch("app.api.views.save_response", save_response)
            )
            for owner, method, stage in (
                (BaseDatabaseWrapper, "connect", "sqlite_connection_open"),
                (BaseDatabaseWrapper, "commit", "result_commit"),
                (DatabaseOperations, "adapt_json_value", "result_payload_encoding"),
                (TripResult, "save", "result_insert"),
            ):
                self.stack.enter_context(
                    patch.object(
                        owner, method, self._timed(getattr(owner, method), stage)
                    )
                )
        except BaseException:
            self.stack.close()
            raise
        return self

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)


def summarize_stages(row, budgets):
    """Keep stage aggregation separate from HTTP campaign ownership."""
    from scripts.nationwide_quality_metrics import percentile

    row["stages_p95_seconds"] = {
        name: percentile([b.stage_seconds.get(name, 0) for b in budgets], 95)
        for name in {name for b in budgets for name in b.stage_seconds}
    }
    row["stage_cpu_seconds"] = {
        name: round(
            sum(b.completed_stage_cpu_seconds.get(name, 0) for b in budgets),
            6,
        )
        for name in {name for b in budgets for name in b.completed_stage_cpu_seconds}
    }
