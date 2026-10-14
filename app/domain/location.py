"""Resolved endpoint facts independent of database and boundary libraries."""

from dataclasses import dataclass

from app.domain.routing import Coordinates


@dataclass(frozen=True, slots=True)
class PlaceCandidate:
    feature_id: str
    name: str
    state: str
    coordinates: Coordinates
    population: int


@dataclass(frozen=True, slots=True)
class ResolvedLocation:
    coordinates: Coordinates
    precision: str
    state: str
    place_id: str | None = None
    name: str | None = None


class LocationOutsideUS(ValueError):
    pass


class LocationNotFound(ValueError):
    pass


@dataclass(slots=True)
class LocationAmbiguous(ValueError):
    candidates: tuple[PlaceCandidate, ...]
    has_more: bool

    def __str__(self) -> str:
        return "multiple US places match this city and state"
