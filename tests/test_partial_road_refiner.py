"""A partial cache can improve a hosted plan without hiding CSV offers."""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.planning.candidates import StationCandidate
from app.planning.national_refiner import NationalRoadRefiner


def _offer(station_id: str, point: Coordinates) -> StationCandidate:
    return StationCandidate(
        station_id, station_id, "CSV road", "CSV city", "TX", point, Decimal(3)
    )


def test_partial_refiner_preserves_distinct_offers_at_completed_point() -> None:
    complete = Coordinates(29.0, -95.0)
    pending = Coordinates(29.2, -95.2)
    offers = (_offer("a", complete), _offer("b", complete), _offer("c", pending))
    candidates = SimpleNamespace(
        version="csv-v1", stations=offers, road_point_hash="points-v1"
    )
    local = SimpleNamespace(graph_version="road-v1")
    refiner = NationalRoadRefiner(
        Path("unused.db"),
        candidates,
        local,
        allow_partial=True,  # type: ignore[arg-type]
    )
    selected = SimpleNamespace(graph=None, retained=offers[:2], unavailable_ids=())
    search = SimpleNamespace(solution=None, expansions=0, complete=True)
    with (
        patch(
            "app.planning.national_refiner.completed_road_points",
            return_value={complete},
        ),
        patch(
            "app.planning.national_refiner.build_trip_road_graph", return_value=selected
        ) as build_graph,
        patch("app.planning.national_refiner.search_fuel_graph", return_value=search),
    ):
        assert (
            refiner.propose(
                Coordinates(29.0, -95.1),
                Coordinates(29.1, -95.0),
                Decimal(1),
                Decimal(0),
                Decimal(1000),
                Decimal(10000),
                None,
                RequestBudget(),
            )
            is None
        )
    assert tuple(offer.station_id for offer in build_graph.call_args.args[1]) == (
        "a",
        "b",
    )


def test_partial_refiner_searches_without_a_hosted_incumbent() -> None:
    point = Coordinates(29.0, -95.0)
    offer = _offer("a", point)
    candidates = SimpleNamespace(
        version="new-price", stations=(offer,), road_point_hash="same-points"
    )
    refiner = NationalRoadRefiner(
        Path("unused.db"),
        candidates,
        SimpleNamespace(graph_version="road-v1"),
        allow_partial=True,
    )
    selected = SimpleNamespace(graph=None, retained=(offer,), unavailable_ids=())
    solution = SimpleNamespace(
        station_ids=("a",),
        purchase_cost=Decimal(5),
        leg_distances_meters=(Decimal(1000), Decimal(1000)),
    )
    search = SimpleNamespace(solution=solution, expansions=1, complete=True)
    with (
        patch(
            "app.planning.national_refiner.completed_road_points", return_value={point}
        ),
        patch(
            "app.planning.national_refiner.build_trip_road_graph", return_value=selected
        ),
        patch("app.planning.national_refiner.search_fuel_graph", return_value=search),
    ):
        proposal = refiner.propose(
            Coordinates(29, -95.1),
            Coordinates(29.1, -95),
            Decimal(1),
            Decimal(0),
            None,
            Decimal(10000),
            None,
            RequestBudget(),
        )
    assert proposal is not None
    assert tuple(item.station.station_id for item in proposal) == ("a",)
