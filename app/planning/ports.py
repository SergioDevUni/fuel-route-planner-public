"""Application-facing contracts for replaceable external dependencies."""

from typing import Protocol

from app.domain.road import RoadDistanceMatrix
from app.domain.routing import Coordinates, Route
from app.planning.budget import RequestBudget

MAX_HOSTED_MATRIX_LOCATIONS = 59
MAX_PUBLIC_MATRIX_LOCATIONS = 59  # 59 squared = 3,481, below 3,500 pairs.


class RoutingPort(Protocol):
    def route(
        self, waypoints: tuple[Coordinates, ...], budget: RequestBudget
    ) -> Route: ...


class RoadMatrixPort(Protocol):
    def matrix(
        self, points: tuple[Coordinates, ...], budget: RequestBudget
    ) -> RoadDistanceMatrix: ...
