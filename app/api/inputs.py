"""Resolve public trip locations from prepared US data."""

from __future__ import annotations

import logging

from app.api.assets import load_boundaries
from app.api.validation import TripInput
from app.domain.location import ResolvedLocation
from app.domain.routing import Coordinates
from app.locations.catalog import DjangoPlaceCatalog
from app.locations.models import GazetteerVersion
from app.locations.resolver import LocationResolver
from app.planning.budget import RequestBudget

logger = logging.getLogger("app.api.service")


def resolve_location(spec: dict, resolver: LocationResolver) -> ResolvedLocation:
    if spec["type"] == "coordinates":
        return resolver.coordinates(Coordinates(spec["latitude"], spec["longitude"]))
    if spec["type"] == "city":
        return resolver.city(spec["city"], spec["state"])
    return resolver.place_id(spec["id"])


def resolve_inputs(
    trip: TripInput, budget: RequestBudget | None = None
) -> tuple[ResolvedLocation, ResolvedLocation]:
    active = GazetteerVersion.objects.filter(status="active").first()
    if active is None:
        raise FileNotFoundError("local place dataset is not ready")
    resolver = LocationResolver(load_boundaries(budget), DjangoPlaceCatalog(active))
    return resolve_location(trip.start, resolver), resolve_location(
        trip.finish, resolver
    )
