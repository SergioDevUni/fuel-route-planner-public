"""Candidate projection and feasible corridor station sequences."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from app.domain.fuel import VehicleSpec
from app.domain.routing import Route
from app.domain.units import METERS_PER_MILE
from app.planning.candidates import ProjectedCandidate
from app.planning.fuel import FuelShortfall
from app.planning.trip_results import calculate_fuel

if TYPE_CHECKING:
    from app.planning.trip_context import PlanningContext

logger = logging.getLogger("app.planning.trip")


def provisional_legs(
    route: Route, candidates: tuple[ProjectedCandidate, ...]
) -> tuple[Decimal, ...]:
    progress = (
        (Decimal(0),)
        + tuple(item.progress_meters for item in candidates)
        + (route.distance_meters,)
    )
    return tuple(b - a for a, b in zip(progress, progress[1:], strict=False))


def compact_corridor(
    baseline: Route,
    corridor: tuple[ProjectedCandidate, ...],
    initial: Decimal,
    reserve: Decimal,
) -> tuple[ProjectedCandidate, ...]:
    """Suggest spaced CSV stops for final road verification on long trips.

    This is a feasible-chain fallback, not a fuel-price cost_search claim.
    Projected distances are screened before the actual routed legs are checked.
    """
    vehicle = VehicleSpec()
    full_range = (
        (vehicle.capacity_gallons - reserve)
        * vehicle.miles_per_gallon
        * METERS_PER_MILE
    )
    first_range = (initial - reserve) * vehicle.miles_per_gallon * METERS_PER_MILE
    if full_range <= 0 or first_range <= 0:
        return ()
    progress = Decimal(0)
    chosen: list[ProjectedCandidate] = []
    while baseline.distance_meters - progress > (
        first_range if not chosen else full_range
    ) * Decimal("0.85"):
        allowance = first_range if not chosen else full_range
        target = progress + allowance * Decimal("0.75")
        candidates = tuple(
            item
            for item in corridor
            if progress < item.progress_meters <= progress + allowance * Decimal("0.9")
            and item not in chosen
        )
        if not candidates or len(chosen) >= 48:
            logger.info(
                "local_first_compact_gap chosen=%d progress=%s allowance=%s",
                len(chosen),
                progress,
                allowance,
            )
            return ()
        next_stop = min(
            candidates,
            key=lambda item: (
                abs(item.progress_meters - target) + item.offset_meters * 4,
                item.station.price_per_gallon,
                item.station.station_id,
            ),
        )
        chosen.append(next_stop)
        progress = next_stop.progress_meters
    try:
        calculate_fuel(
            tuple(item.station for item in chosen),
            provisional_legs(baseline, tuple(chosen)),
            initial,
            reserve,
        )
    except FuelShortfall:
        logger.info("local_first_compact_provisional_shortfall stops=%d", len(chosen))
        return ()
    return tuple(chosen)


def choose_corridor(
    context: PlanningContext,
    baseline: Route,
    initial: Decimal,
    reserve: Decimal,
    excluded_ids: frozenset[str] = frozenset(),
) -> tuple[ProjectedCandidate, ...]:
    for radius in (8_000.0, 32_000.0):
        candidates = tuple(
            item
            for item in context.along_route(baseline, radius)
            if item.station.station_id not in excluded_ids
            and item.progress_meters > 0
            and item.progress_meters < baseline.distance_meters
        )
        if len(candidates) > 1_000:
            candidates = tuple(
                sorted(
                    candidates,
                    key=lambda item: (
                        item.offset_meters,
                        item.station.price_per_gallon,
                    ),
                )[:1_000]
            )
            candidates = tuple(
                sorted(candidates, key=lambda item: item.progress_meters)
            )
        try:
            provisional = calculate_fuel(
                tuple(item.station for item in candidates),
                provisional_legs(baseline, candidates),
                initial,
                reserve,
            )
        except FuelShortfall:
            continue
        selected = tuple(
            item
            for item, visit in zip(candidates, provisional.visits, strict=True)
            if visit.purchased_gallons > 0
        )
        if len(selected) <= 48:
            return selected
    return ()
