"""Directed road distances returned by a routing engine."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RoadDistanceMatrix:
    """Meters of fastest driving routes; None means unreachable."""

    distances_meters: tuple[tuple[Decimal | None, ...], ...]
    source_snap_meters: tuple[Decimal, ...]
    destination_snap_meters: tuple[Decimal, ...]
    durations_seconds: tuple[tuple[Decimal | None, ...], ...] | None = None
