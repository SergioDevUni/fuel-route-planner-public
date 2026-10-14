"""Native batch equivalence and deadline/cancellation boundaries."""

import random
from decimal import Decimal

import numpy as np
import pytest

from app.domain.routing import Coordinates
from app.planning.budget import PlanningDeadlineExceeded, RequestBudget
from app.planning.candidates import (
    CandidateIndex,
    StationCandidate,
    _batch_encounters,
    _prepare_geometry,
    _project_encounters,
    discovery_context,
)


def test_batch_projection_matches_per_station_reference_across_loops_and_batches():
    rng = random.Random(972301)
    points = tuple(
        Coordinates(40 + rng.uniform(-0.2, 0.2), -90 + rng.uniform(-0.2, 0.2))
        for _ in range(200)
    )
    points = points[:20] + (points[19],) + points[20:] + points[:20]
    tree, lengths, cumulative, coordinates = _prepare_geometry(points)
    stations = tuple(
        StationCandidate(
            str(i),
            str(i),
            "Road",
            "Example",
            "IL",
            Coordinates(40 + rng.uniform(-0.2, 0.2), -90 + rng.uniform(-0.2, 0.2)),
            Decimal(3),
        )
        for i in range(260)
    )
    index = CandidateIndex.__new__(CandidateIndex)
    index.stations = stations
    for corridor in (100, 2500, 8000):
        actual = dict(
            (s.station_id, v)
            for s, v in _batch_encounters(
                tree,
                coordinates,
                stations,
                corridor,
                lengths,
                cumulative,
                index._positions,
            )
        )
        for station in stations:
            expected = _project_encounters(
                coordinates,
                np.arange(len(coordinates), dtype=np.int64),
                station.coordinates,
                corridor,
                lengths,
                cumulative,
            )
            found = actual.get(station.station_id, [])
            assert [v[0] for v in found] == [v[0] for v in expected]
            for first, last in zip(found, expected, strict=True):
                assert first[1:] == pytest.approx(last[1:], abs=1e-7)


def test_batch_discovery_interrupts_between_encounters_without_cancelling_parent():
    points = (Coordinates(40, -90), Coordinates(40, -89.9))
    tree, lengths, cumulative, coordinates = _prepare_geometry(points)
    stations = tuple(
        StationCandidate(str(i), str(i), "Road", "Example", "IL", points[0], Decimal(3))
        for i in range(3)
    )
    root = RequestBudget()
    child = root.child(5)
    index = CandidateIndex.__new__(CandidateIndex)
    index.stations = stations
    with discovery_context(child, "local"):
        stream = _batch_encounters(
            tree, coordinates, stations, 8000, lengths, cumulative, index._positions
        )
        next(stream)
        child.stop_branch()
        with pytest.raises(PlanningDeadlineExceeded):
            next(stream)
    root.check_deadline()
    root.consume_attempt()
    assert root.attempts == child.attempts == 1


def test_stopping_parent_branch_stops_nested_children_but_not_sibling():
    root = RequestBudget()
    branch = root.child(5)
    nested = branch.child(3)
    sibling = root.child(5)
    branch.stop_branch()
    with pytest.raises(PlanningDeadlineExceeded):
        nested.check_deadline()
    sibling.check_deadline()
    root.check_deadline()
