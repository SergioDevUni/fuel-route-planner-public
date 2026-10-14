"""First-stop bounds must cover every surviving offer and valid snapped endpoint."""

from decimal import Decimal
from types import SimpleNamespace

from app.domain.road import RoadDistanceMatrix
from app.domain.routing import Coordinates
from scripts.resolve_first_stop_rejections import (
    exclude_first_stops,
    possible_first_offers,
)


def matrix(via, station_snap=Decimal(0)):
    values = (
        (Decimal(0), Decimal(100), Decimal(500)),
        (Decimal(100), Decimal(0), Decimal(via)),
        (Decimal(500), Decimal(via), Decimal(0)),
    )
    snaps = (Decimal(0), station_snap, Decimal(0))
    return RoadDistanceMatrix(values, snaps, snaps, values)


def test_time_exclusion_compares_all_first_stops_to_baseline_plus_allowance():
    stations = (SimpleNamespace(station_id="first"),)
    result = exclude_first_stops(matrix(2300), stations, 2, Decimal(500))
    assert result["status"] == "checked_infeasible"
    assert result["first_offers"][0]["minimum_extra_seconds"] == "1900"


def test_possible_first_stop_or_invalid_snap_keeps_classification_undetermined():
    stations = (SimpleNamespace(station_id="first"),)
    assert (
        exclude_first_stops(matrix(2200), stations, 2, Decimal(500))["status"]
        == "undetermined"
    )
    assert (
        exclude_first_stops(matrix(2300, Decimal(251)), stations, 2, Decimal(500))[
            "status"
        ]
        == "undetermined"
    )


def test_geodesic_screen_retains_every_offer_including_shared_point_ids():
    start = Coordinates(34, -118)
    near = Coordinates(34, -117)
    stations = tuple(
        SimpleNamespace(station_id=key, coordinates=near) for key in ("a", "b")
    ) + (SimpleNamespace(station_id="far", coordinates=Coordinates(40, -100)),)
    assert [
        s.station_id
        for s in possible_first_offers(start, stations, Decimal(20), Decimal(2))
    ] == ["a", "b"]
