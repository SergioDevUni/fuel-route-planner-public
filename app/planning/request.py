"""Immutable resolved trip inputs shared by planning strategies."""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.routing import Coordinates
from app.planning.trip_policy import validate_policy


@dataclass(frozen=True, slots=True)
class PlanningRequest:
    start: Coordinates
    finish: Coordinates
    initial: Decimal
    reserve: Decimal
    max_detour_miles: Decimal | None
    max_detour_ratio: Decimal | None
    initial_price: Decimal | None = None
    optimization_mode: str = "balanced"
    max_extra_duration_minutes: Decimal = Decimal(30)

    def __post_init__(self) -> None:
        validate_policy(self.optimization_mode, self.max_extra_duration_minutes)
