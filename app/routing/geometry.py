"""WGS84 distance adapter; the domain does not depend on pyproj."""

from decimal import Decimal

from pyproj import Geod

from app.domain.routing import Coordinates

WGS84 = Geod(ellps="WGS84")


def geodesic_meters(start: Coordinates, finish: Coordinates) -> Decimal:
    _, _, distance = WGS84.inv(
        start.longitude, start.latitude, finish.longitude, finish.latitude
    )
    return Decimal(str(distance))
