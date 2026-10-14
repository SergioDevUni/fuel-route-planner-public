"""Record normalized road facts and replay without external provider calls."""

import json
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from time import perf_counter, sleep
from unittest.mock import patch

from app.domain.road import RoadDistanceMatrix
from app.domain.routing import Coordinates, Route, RouteLeg, RouteWaypoint
from app.planning.matrix_cache import CachedRoadMatrixProvider
from app.routing.client import OpenRouteServiceAdapter, RoutingUnavailable


def road_key(kind, points):
    return json.dumps([kind, [point.as_provider_pair() for point in points]])


def encode(value):
    if isinstance(value, (Decimal, datetime)):
        return str(value)
    raise TypeError(f"unsupported road fact {type(value)}")


def decode_route(data):
    return Route(
        geometry=tuple(Coordinates(**item) for item in data["geometry"]),
        waypoints=tuple(
            RouteWaypoint(
                Coordinates(**item["requested"]),
                Coordinates(**item["routed"]),
                item["geometry_index"],
                Decimal(item["snap_distance_meters"]),
            )
            for item in data["waypoints"]
        ),
        legs=tuple(
            RouteLeg(
                Decimal(item["distance_meters"]), Decimal(item["duration_seconds"])
            )
            for item in data["legs"]
        ),
        distance_meters=Decimal(data["distance_meters"]),
        duration_seconds=Decimal(data["duration_seconds"]),
        engine_version=data["engine_version"],
        fetched_at=datetime.fromisoformat(data["fetched_at"]),
    )


def decode_matrix(data):
    def rows(values):
        return tuple(
            tuple(Decimal(value) if value is not None else None for value in row)
            for row in values
        )

    return RoadDistanceMatrix(
        rows(data["distances_meters"]),
        tuple(Decimal(item) for item in data["source_snap_meters"]),
        tuple(Decimal(item) for item in data["destination_snap_meters"]),
        rows(data["durations_seconds"])
        if data["durations_seconds"] is not None
        else None,
    )


class RoadReplay:
    """Unknown point sequences fail explicitly; never fabricate a road route."""

    def __init__(self, record: Path | None = None, replay: Path | None = None):
        if record is not None and replay is not None:
            raise ValueError("recording and replay are mutually exclusive")
        self.record, self.replay = record, replay
        self.guard = Lock()
        self.entries = {}
        self.stack = ExitStack()
        if replay is not None:
            payload = json.loads(replay.read_text(encoding="utf-8"))
            if payload["schema"] != 1:
                raise ValueError("unsupported road replay schema")
            self.entries = payload["entries"]

    def __enter__(self):
        if self.record is None and self.replay is None:
            return self
        for cls, method, kind, decode in (
            (OpenRouteServiceAdapter, "route", "route", decode_route),
            (CachedRoadMatrixProvider, "matrix", "matrix", decode_matrix),
        ):
            original = getattr(cls, method)

            def wrapped(
                provider, points, budget, kind=kind, decode=decode, original=original
            ):
                key = road_key(kind, points)
                if self.replay is not None:
                    budget.consume_attempt()
                    entry = self.entries.get(key)
                    if entry is None:
                        budget.increment_metric("road_replay_missing", 1)
                        raise RoutingUnavailable(
                            "recorded road sequence is unavailable"
                        )
                    with budget.stage("recorded_road_io"):
                        end = perf_counter() + entry["seconds"]
                        while perf_counter() < end:
                            budget.check_deadline()
                            sleep(min(0.02, max(0, end - perf_counter())))
                        result = decode(entry["facts"])
                    budget.check_deadline()
                    return result
                began = perf_counter()
                result = original(provider, points, budget)
                entry = {"seconds": perf_counter() - began, "facts": asdict(result)}
                with self.guard:
                    self.entries[key] = entry
                return result

            self.stack.enter_context(patch.object(cls, method, wrapped))
        return self

    def __exit__(self, *exc):
        self.stack.close()
        if self.record is not None:
            self.record.parent.mkdir(parents=True, exist_ok=True)
            self.record.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "scope": "normalized road facts/delays; no prices or secrets",
                        "entries": self.entries,
                    },
                    default=encode,
                ),
                encoding="utf-8",
            )
