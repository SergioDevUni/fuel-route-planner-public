"""Resolve, cache and coordinate public trip calculations."""

from __future__ import annotations

import hashlib
import json
import logging

from app.api.assets import current_candidate_hash
from app.api.cache import trip_cache, verified_incumbent_cache
from app.api.execution import calculate_uncached
from app.api.inputs import resolve_inputs
from app.api.routing_config import national_local_config, partial_road_config
from app.api.validation import TripInput
from app.domain.location import ResolvedLocation
from app.locations.models import GazetteerVersion
from app.planning.budget import RequestBudget
from app.stations.models import DatasetVersion

logger = logging.getLogger("app.api.service")


def timed_calculate_uncached(
    trip: TripInput,
    candidate_hash: str | None,
    start: ResolvedLocation,
    finish: ResolvedLocation,
    budget: RequestBudget,
) -> dict:
    with budget.stage("plan_execution"):
        return calculate_uncached(trip, candidate_hash, start, finish, budget)


def calculate(trip: TripInput, budget: RequestBudget | None = None) -> dict:
    budget = budget or RequestBudget()
    budget.configure_mode(trip.optimization_mode)
    with budget.stage("input_resolution"):
        start, finish = resolve_inputs(trip, budget)
    budget.check_deadline()
    if start.coordinates == finish.coordinates:
        return calculate_uncached(trip, None, start, finish, budget)
    keys = calculation_keys(trip, budget)
    if keys is None:
        return calculate_uncached(trip, None, start, finish, budget)
    key, feasible_key, candidate_hash = keys
    for profile, (rank, payload) in verified_incumbent_cache.get(feasible_key).items():
        budget.publish_verified_payload(payload, rank, profile, from_cache=True)
    budget.set_payload_sink(
        lambda profile, rank, payload: verified_incumbent_cache.put(
            feasible_key, profile, rank, payload
        )
    )
    budget.set_completed_payload_sink(
        lambda payload: trip_cache.put_completed(key, payload)
    )
    result = trip_cache.get_or_compute(
        key,
        lambda: timed_calculate_uncached(trip, candidate_hash, start, finish, budget),
        budget,
    )
    budget.check_deadline()
    return result


def calculation_keys(
    trip: TripInput, budget: RequestBudget
) -> tuple[str, str, str] | None:
    with budget.stage("request_setup"):
        active = DatasetVersion.objects.filter(status="active").first()
        gazetteer = GazetteerVersion.objects.filter(status="active").first()
        if active is None or gazetteer is None:
            return None
        candidate_hash = current_candidate_hash()
        key_parts = {
            "request": {
                "start": trip.start,
                "finish": trip.finish,
                "initial": str(trip.initial_fuel_gallons),
                "initial_price": str(trip.initial_fuel_price_per_gallon),
                "reserve": str(trip.reserve_gallons),
                "max_detour_miles": str(trip.max_detour_miles),
                "max_detour_ratio": str(trip.max_detour_ratio),
                "optimization_mode": trip.optimization_mode,
                "max_extra_duration_minutes": str(trip.max_extra_duration_minutes),
            },
            "price_source": active.source_sha256,
            "gazetteer": gazetteer.source_sha256,
            "candidates": candidate_hash,
            "planner_policy": "v37-optimization-modes",
            **budget.settings.routing_identity(),
            "national_road_ready": national_local_config(
                candidate_hash, budget.settings
            )
            is not None,
            "partial_road_source_count": (
                partial_road_config(candidate_hash, budget.settings)
                or (None, None, None, 0)
            )[3],
        }
    key = hashlib.sha256(json.dumps(key_parts, sort_keys=True).encode()).hexdigest()
    # Growing source availability can improve cost, but does not invalidate a
    # route already verified with the same inputs, CSV, road and policy versions.
    feasible_key = hashlib.sha256(
        json.dumps(
            {k: v for k, v in key_parts.items() if k != "partial_road_source_count"},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return key, feasible_key, candidate_hash
