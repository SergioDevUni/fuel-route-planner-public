"""Bounded price-aware alternatives for the first road-verified stop chain."""

from decimal import ROUND_CEILING, Decimal

from app.domain.fuel import VehicleSpec
from app.domain.routing import Route
from app.domain.units import METERS_PER_MILE
from app.planning.budget import RequestBudget
from app.planning.candidates import ProjectedCandidate
from app.planning.corridor_selection import provisional_legs
from app.planning.fuel import FuelShortfall
from app.planning.trip_results import calculate_fuel


def initial_corridor(
    baseline: Route,
    corridor: tuple[ProjectedCandidate, ...],
    spaced: tuple[ProjectedCandidate, ...],
    initial: Decimal,
    reserve: Decimal,
    budget: RequestBudget,
) -> tuple[ProjectedCandidate, ...]:
    """Remove low-value stops while keeping the original spaced fallback.

    Projection supplies a proposal only; actual roads, fuel and driving time
    are checked before publication. No provider query is made here.
    """
    full_range = (
        (VehicleSpec().capacity_gallons - reserve)
        * VehicleSpec().miles_per_gallon
        * METERS_PER_MILE
    )
    if full_range <= 0:
        return spaced
    # Keep a small number of price alternatives beyond the minimum fuel chain.
    # A fixed twelve-stop seed adds unnecessary road access on regional trips.
    minimum_stops = int(
        (baseline.distance_meters / full_range).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    limit = min(12, max(2, minimum_stops + 2))
    candidates = tuple(
        item for item in corridor if 0 < item.progress_meters < baseline.distance_meters
    )
    try:
        provisional = calculate_fuel(
            tuple(item.station for item in candidates),
            provisional_legs(baseline, candidates),
            initial,
            reserve,
        )
    except FuelShortfall:
        return spaced
    priced = tuple(
        item
        for item, visit in zip(candidates, provisional.visits, strict=True)
        if visit.purchased_gallons > 0
    )

    def rank(
        items: tuple[ProjectedCandidate, ...],
    ) -> tuple[Decimal, Decimal, int, tuple[str, ...]]:
        fuel = calculate_fuel(
            tuple(item.station for item in items),
            provisional_legs(baseline, items),
            initial,
            reserve,
        )
        # Include a two-way access fuel estimate in this heuristic's ranking.
        # It avoids saving pennies by adding distant stations to the first plan.
        access_cost = sum(
            (
                2
                * item.offset_meters
                * item.station.price_per_gallon
                / (VehicleSpec().miles_per_gallon * METERS_PER_MILE)
                for item in items
            ),
            Decimal(0),
        )
        return (
            fuel.exact_purchase_cost + access_cost,
            fuel.exact_purchase_cost,
            len(items),
            tuple(item.station.station_id for item in items),
        )

    best = spaced
    best_rank = rank(spaced) if spaced else None
    selected = priced
    while len(selected) > limit:
        budget.check_deadline()
        alternatives = []
        for position in range(len(selected)):
            budget.check_deadline()
            proposal = selected[:position] + selected[position + 1 :]
            try:
                alternatives.append((rank(proposal), position))
            except FuelShortfall:
                continue
        if not alternatives:
            return best or priced
        _, remove = min(alternatives)
        selected = selected[:remove] + selected[remove + 1 :]
    if selected:
        selected_rank = rank(selected)
        if best_rank is None or selected_rank < best_rank:
            best = selected
    if best != spaced:
        budget.increment_metric("price_aware_initial_chains", 1)
    return best or priced
