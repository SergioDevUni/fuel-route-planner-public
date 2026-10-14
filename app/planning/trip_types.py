"""Verified trip results and planning failure types."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.fuel import FuelPlan
from app.domain.ledger import CostLedger
from app.domain.routing import Route
from app.planning.candidates import StationCandidate


class NoFeasiblePlan(ValueError):
    pass


class RouteCheckFailed(ValueError):
    pass


class DetourLimitExceeded(ValueError):
    pass


class DurationLimitExceeded(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PlannedTrip:
    baseline: Route
    verified: Route
    routed_stations: tuple[StationCandidate, ...]
    fuel: FuelPlan
    ledger: CostLedger
    initial_price: Decimal | None
    initial_price_basis: str
    candidate_version: str
    outbound_attempts: int
    selection_method: str
    matrix_search_complete: bool | None = None
