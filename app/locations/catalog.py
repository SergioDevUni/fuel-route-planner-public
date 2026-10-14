"""Django lookup adapter for the active GeoNames version."""

from app.domain.location import PlaceCandidate
from app.domain.routing import Coordinates
from app.locations.models import GazetteerVersion, Place, PlaceAlias


class DjangoPlaceCatalog:
    def __init__(self, version: GazetteerVersion) -> None:
        if version.status != "active":
            raise ValueError("gazetteer version is not active")
        self._version_id = version.pk

    def find_by_name(
        self, normalized_name: str, state: str, limit: int = 21
    ) -> tuple[PlaceCandidate, ...]:
        aliases = (
            PlaceAlias.objects.filter(
                place__version_id=self._version_id,
                normalized_name=normalized_name,
                state=state,
            )
            .select_related("place")
            .order_by("-place__population", "place__feature_id")[:limit]
        )
        return tuple(_candidate(alias.place) for alias in aliases)

    def find_by_id(self, feature_id: str) -> PlaceCandidate | None:
        place = Place.objects.filter(
            version_id=self._version_id, feature_id=feature_id
        ).first()
        return _candidate(place) if place is not None else None


def _candidate(place: Place) -> PlaceCandidate:
    return PlaceCandidate(
        feature_id=place.feature_id,
        name=place.name,
        state=place.state,
        coordinates=Coordinates(place.latitude, place.longitude),
        population=place.population,
    )
