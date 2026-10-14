"""Conservative purchase bounds independent of candidate search and road coverage."""

from collections.abc import Iterable
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from app.domain.fuel import VehicleSpec
from app.domain.routing import Coordinates
from app.planning.candidates import StationCandidate
from app.routing.geometry import geodesic_meters


def offer_coverage(
    loaded: Iterable[str],
    eligible: Iterable[str],
    retained: Iterable[str],
    road_data_queue: Iterable[str] = (),
) -> dict[str, int]:
    """Account for all CSV offers without treating omitted offers as infeasible."""
    all_ids, eligible_ids, retained_ids = set(loaded), set(eligible), set(retained)
    queued_ids = set(road_data_queue)
    if not retained_ids <= eligible_ids <= all_ids or not queued_ids <= eligible_ids:
        raise ValueError("search offer coverage differs from loaded CSV universe")
    return {
        "loaded_csv_offers": len(all_ids),
        "safely_excluded_by_cost": len(all_ids - eligible_ids),
        "eligible_csv_offers": len(eligible_ids),
        "retained_graph_offers": len(retained_ids),
        "additional_eligible_offers": len(eligible_ids - retained_ids),
        "retained_offers_awaiting_road_data": len(queued_ids & retained_ids),
    }


def purchase_bound(
    start: Coordinates,
    finish: Coordinates,
    stations: Iterable[StationCandidate],
    initial: Decimal,
    reserve: Decimal,
    upper: Decimal,
) -> dict[str, Any]:
    offers = tuple(stations)
    # Public verification permits at most 1 km of snapping at each endpoint.
    # Relax road distance, tank capacity, directed connectivity and time limits.
    distance = max(Decimal(0), geodesic_meters(start, finish) - Decimal(2001))
    gallons = max(
        Decimal(0), VehicleSpec().gallons_for_meters(distance) + reserve - initial
    )
    cheapest = min((v.price_per_gallon for v in offers), default=Decimal(0))
    lower = (gallons * cheapest).quantize(Decimal("0.00000001"), ROUND_FLOOR)
    # A negative gap indicates a violated bound assumption; do not hide it.
    if lower > upper + Decimal("0.00000001"):
        raise ValueError("verified purchase cost violates relaxed lower bound")
    gap = max(Decimal(0), upper - lower)
    return {
        "scope": "all_loaded_csv_offers_relaxed_geometry",
        "priced_offer_count": len(offers),
        "lower_bound_usd": str(lower),
        "verified_upper_bound_usd": str(upper),
        "absolute_gap_usd": str(gap),
        "relative_gap_percent": str(gap / lower * 100) if lower > 0 else None,
        "assumptions": "Fixed vehicle; all loaded CSV prices; endpoint snaps <=1km; "
        "roads, tank capacity, time limits and connectivity relaxed. "
        "Missing road edges do not imply infeasibility.",
    }
