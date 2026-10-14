"""Resolve supported endpoint inputs locally without geocoding calls."""

from typing import Protocol

from app.domain.location import (
    LocationAmbiguous,
    LocationNotFound,
    LocationOutsideUS,
    PlaceCandidate,
    ResolvedLocation,
)
from app.domain.routing import Coordinates
from app.domain.text import canonical_text
from app.locations.states import state_code


class BoundaryPort(Protocol):
    def state_for(self, coordinates: Coordinates) -> str | None: ...


class PlaceCatalog(Protocol):
    def find_by_name(
        self, normalized_name: str, state: str, limit: int = 21
    ) -> tuple[PlaceCandidate, ...]: ...

    def find_by_id(self, feature_id: str) -> PlaceCandidate | None: ...


class LocationResolver:
    def __init__(self, boundaries: BoundaryPort, places: PlaceCatalog) -> None:
        self.load_boundaries = boundaries
        self._places = places

    def coordinates(self, point: Coordinates) -> ResolvedLocation:
        state = self.load_boundaries.state_for(point)
        if state is None:
            raise LocationOutsideUS("coordinate is outside the 50 states and DC")
        return ResolvedLocation(point, "coordinates", state)

    def city(self, name: str, state: str) -> ResolvedLocation:
        normalized = canonical_text(name)
        if not normalized or len(name) > 200:
            raise ValueError("city name must be nonempty and at most 200 characters")
        code = state_code(state)
        matches = self._places.find_by_name(normalized, code)
        if not matches:
            raise LocationNotFound("city was not found in the local gazetteer")
        if len(matches) > 1:
            raise LocationAmbiguous(matches[:20], len(matches) > 20)
        return self._resolved_place(matches[0])

    def place_id(self, feature_id: str) -> ResolvedLocation:
        place = self._places.find_by_id(feature_id)
        if place is None:
            raise LocationNotFound("place ID was not found in the local gazetteer")
        return self._resolved_place(place)

    def _resolved_place(self, place: PlaceCandidate) -> ResolvedLocation:
        region = self.load_boundaries.state_for(place.coordinates)
        if region is None or region != place.state:
            raise LocationOutsideUS("place point does not match its US state boundary")
        return ResolvedLocation(
            coordinates=place.coordinates,
            precision="city",
            state=place.state,
            place_id=place.feature_id,
            name=place.name,
        )
