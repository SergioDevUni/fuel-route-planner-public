"""Allowlisted operational logging, without URLs, request bodies or tokens."""

import json
import logging


class OperationalFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Exception messages/tracebacks can contain provider URLs or credentials.
        fields = {"event": record.msg, "level": record.levelname}
        for name in (
            "request_id",
            "endpoint",
            "method",
            "status",
            "duration_seconds",
            "error_code",
            "exception_type",
            "active_planners",
            "late_planners",
            "waiting_planners",
            "accepting_work",
        ):
            if hasattr(record, name):
                fields[name] = getattr(record, name)
        return json.dumps(fields, separators=(",", ":"))
