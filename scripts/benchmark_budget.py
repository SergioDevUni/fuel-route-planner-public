"""Persistent campaign-wide hosted-call accounting, outside measured timers."""

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

from app.routing.client import OpenRouteServiceAdapter, RoutingQuotaExceeded
from scripts.atomic_checkpoint import write_checkpoint


class BenchmarkPaused(RuntimeError):
    pass


class BenchmarkBudget:
    """One active process owns a ledger; its provider threads share this guard."""

    def __init__(
        self, path: Path, limit=1000, batch_limit=250, per_minute=30, stop_at=None
    ):
        if min(limit, batch_limit, per_minute) < 1:
            raise ValueError("benchmark limits must be positive")
        self.path, self.limit = path, limit
        self.stop_at = min(limit, stop_at) if stop_at is not None else limit
        self.batch_limit, self.per_minute = batch_limit, per_minute
        self.guard = threading.Lock()
        self.batch_start = 0
        self.data = (
            json.loads(path.read_text())
            if path.exists()
            else {"schema": 1, "limit": limit, "attempts": 0, "calls": []}
        )
        if self.data["limit"] != limit:
            raise ValueError("campaign limit differs from the persisted ledger")
        self.batch_start = self.data["attempts"]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_checkpoint(self.path, self.data)

    def before_request(self, reserve_attempts=4):
        """Reserve worst-case traffic before starting the HTTP stopwatch."""
        reserve_attempts = max(4, reserve_attempts)
        if reserve_attempts > self.per_minute:
            raise ValueError("batch reservation exceeds the minute allowance")
        while True:
            with self.guard:
                if self.data.get("provider_quota_exhausted"):
                    raise BenchmarkPaused(
                        "provider quota exhausted; checkpoint retained"
                    )
                if self.data["attempts"] + reserve_attempts > self.stop_at:
                    raise BenchmarkPaused(
                        "campaign call cap reached; checkpoint retained"
                    )
                if (
                    self.data["attempts"] - self.batch_start + reserve_attempts
                    > self.batch_limit
                ):
                    self.save()
                    self.batch_start = self.data["attempts"]
                now = time.time()
                self.data["calls"] = [v for v in self.data["calls"] if v > now - 61]
                if len(self.data["calls"]) + reserve_attempts <= self.per_minute:
                    return
                delay = max(0.01, self.data["calls"][0] + 61 - now)
            time.sleep(min(1, delay))

    def record(self, attempts):
        # Count actual _post attempts instead of inferred HTTP-request budgets.
        pass

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_suffix(".lock")
        try:
            self.lease = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise BenchmarkPaused("another process owns the campaign ledger") from error
        os.write(self.lease, str(os.getpid()).encode())
        original = OpenRouteServiceAdapter._post

        def counted(adapter, url, body, budget):
            budget.check_deadline()
            with self.guard:
                if self.data["attempts"] >= self.limit:
                    raise BenchmarkPaused("campaign call cap reached")
                now = time.time()
                active = [v for v in self.data["calls"] if v > now - 61]
                if len(active) >= self.per_minute:
                    raise BenchmarkPaused("minute reservation exhausted")
                self.data["calls"] = [*active, now]
                self.data["attempts"] += 1
                self.save()
            try:
                return original(adapter, url, body, budget)
            except RoutingQuotaExceeded:
                with self.guard:
                    self.data["provider_quota_exhausted"] = True
                    self.save()
                raise

        self.patch = patch.object(OpenRouteServiceAdapter, "_post", counted)
        self.patch.start()
        return self

    def __exit__(self, *exception):
        self.patch.stop()
        self.save()
        os.close(self.lease)
        self.lock_path.unlink()
