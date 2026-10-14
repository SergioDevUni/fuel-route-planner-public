"""Generic geometry reuse and bounded baseline transport recovery."""

import math
import random
from decimal import Decimal

import numpy as np
import pytest

from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.planning.candidates import _prepare_geometry, _project_encounters
from app.planning.trip import TripPlanner
from app.routing.client import RoutingAuthenticationFailed, RoutingTransportUnavailable
from tests.support.geometry import _route
from tests.support.planning import FINISH, START, FakeCandidates, FakeMatrixRouter


def test_exact_geometry_cache_reuses_segments_without_simplifying_loops():
    _prepare_geometry.cache_clear()
    geometry = _route().geometry
    first = _prepare_geometry(geometry)
    second = _prepare_geometry(tuple(geometry))
    assert first is second
    assert len(first[1]) == len(geometry) - 1
    assert _prepare_geometry.cache_info().hits == 1
    assert _prepare_geometry(tuple(reversed(geometry))) is not first


def test_one_transient_baseline_retry_keeps_matrix_and_verification_within_four_calls():
    class RoutingPort(FakeMatrixRouter):
        def route(self, points, budget):
            if budget.attempts == 0:
                budget.consume_attempt()
                raise RoutingTransportUnavailable("transient transport failure")
            return super().route(points, budget)

    router, budget = RoutingPort(), RequestBudget()
    result = TripPlanner(router, FakeCandidates(), router).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        None,
        None,
        Decimal(3),
        matrix_first=True,
        budget=budget,
    )
    assert result.outbound_attempts == budget.attempts == 4
    assert result.verified
    assert "baseline_recovery" in budget.stage_seconds


def test_rejected_key_is_not_a_transient_retry():
    class RoutingPort(FakeMatrixRouter):
        def route(self, points, budget):
            budget.consume_attempt()
            raise RoutingAuthenticationFailed("key rejected")

    router, budget = RoutingPort(), RequestBudget()
    with pytest.raises(RoutingAuthenticationFailed):
        TripPlanner(router, FakeCandidates(), router).plan(
            START,
            FINISH,
            Decimal(50),
            Decimal(0),
            None,
            None,
            Decimal(3),
            matrix_first=True,
            budget=budget,
        )
    assert budget.attempts == 1


def test_numeric_projection_preserves_scalar_encounters_and_loop_groups():
    rng = random.Random(781099)
    points = tuple(
        Coordinates(40 + rng.uniform(-0.1, 0.1), -90 + rng.uniform(-0.1, 0.1))
        for _ in range(100)
    )
    # Include degenerate segments and disconnected nearby runs.
    points = points[:20] + (points[19],) + points[20:]
    _, lengths, cumulative, coordinates = _prepare_geometry(points)
    assert not coordinates.flags.writeable
    for _ in range(100):
        point = Coordinates(40 + rng.uniform(-0.1, 0.1), -90 + rng.uniform(-0.1, 0.1))
        corridor = rng.uniform(100, 6000)
        indices = sorted(rng.sample(range(len(points) - 1), 70))
        encounters = []
        for index in indices:
            a, b = points[index : index + 2]
            east = 111_320 * math.cos(math.radians(point.latitude))
            ax, ay = (
                (a.longitude - point.longitude) * east,
                (a.latitude - point.latitude) * 110_574,
            )
            bx, by = (
                (b.longitude - point.longitude) * east,
                (b.latitude - point.latitude) * 110_574,
            )
            denominator = (bx - ax) ** 2 + (by - ay) ** 2
            fraction = (
                min(1, max(0, -(ax * (bx - ax) + ay * (by - ay)) / denominator))
                if denominator
                else 0
            )
            distance = math.hypot(ax + fraction * (bx - ax), ay + fraction * (by - ay))
            if distance <= corridor:
                encounters.append(
                    (index, cumulative[index] + fraction * lengths[index], distance)
                )
        expected = []
        for index, progress, distance in encounters:
            if expected and index <= expected[-1][0] + 1:
                previous = expected[-1]
                expected[-1] = (
                    (index, progress, distance)
                    if distance < previous[2]
                    else (index, previous[1], previous[2])
                )
            else:
                expected.append((index, progress, distance))
        actual = _project_encounters(
            coordinates,
            np.asarray(indices, dtype=np.int64),
            point,
            corridor,
            lengths,
            cumulative,
        )
        assert [v[0] for v in actual] == [v[0] for v in expected]
        for left, right in zip(actual, expected, strict=True):
            assert left[1] == pytest.approx(right[1], abs=1e-7)
            assert left[2] == pytest.approx(right[2], abs=1e-7)
