"""Pending source queries preserve prices, directed legs and unknown scope."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.routing import Coordinates, RouteLeg, RouteWaypoint
from app.planning.budget import RequestBudget
from app.planning.candidates import ProjectedCandidate, StationCandidate
from app.planning.endpoint_block_cache import EndpointBlockCache
from app.planning.graph_search import search_fuel_graph
from app.planning.national_refiner import NationalRoadRefiner
from app.planning.query_road_graph import build_query_road_graph
from app.routing.local_valhalla import LocalRoadBlock
from tests.support.geometry import _route


def test_query_graph_needs_no_completed_source_and_preserves_colocated_fuel(tmp_path):
    baseline = _route()
    start = baseline.waypoints[0].requested
    offers = tuple(
        ProjectedCandidate(
            StationCandidate(s, s, "Road", "Example", "IL", start, Decimal(p)),
            Decimal(0),
            Decimal(0),
            s,
        )
        for s, p in (("a", "4"), ("b", "2"))
    )
    calls = []

    def matrix(sources, targets, budget):
        calls.append((sources, targets))
        return LocalRoadBlock(
            tuple(tuple(Decimal(100000) for _ in targets) for _ in sources),
            tuple(tuple(Decimal(1000) for _ in targets) for _ in sources),
        )

    local = SimpleNamespace(
        graph_version="arbitrary-road-v1", max_points=2, matrix_block=matrix
    )
    cache = EndpointBlockCache(tmp_path, local)
    budget = RequestBudget()
    result = build_query_road_graph(
        baseline, offers, ("b",), local, cache, Decimal(0), Decimal(0), budget, 2
    )
    assert result is not None
    assert {s.station_id for s in result.retained} == {"a", "b"}
    assert len(calls) == 1
    search = search_fuel_graph(result.graph, Decimal(0), Decimal(0))
    assert search.solution is not None
    assert search.solution.station_ids == ("b",)
    assert budget.attempts == 0
    assert result.unknown_ids == ()  # Only the queried subset, never nationwide.
    # Price-only changes reuse road facts and preserve the new CSV offers.
    changed = tuple(
        ProjectedCandidate(
            StationCandidate(
                v.station.station_id,
                v.station.name,
                v.station.address,
                v.station.city,
                v.station.state,
                start,
                Decimal(1) if v.station.station_id == "a" else Decimal(5),
            ),
            v.progress_meters,
            v.offset_meters,
            v.occurrence_id,
        )
        for v in offers
    )
    result2 = build_query_road_graph(
        baseline,
        changed,
        ("a",),
        local,
        cache,
        Decimal(0),
        Decimal(0),
        RequestBudget(),
        2,
    )
    assert result2 is not None
    assert len(calls) == 1
    assert search_fuel_graph(
        result2.graph, Decimal(0), Decimal(0)
    ).solution.station_ids == ("a",)


def test_expired_query_window_keeps_unfinished_offers_unknown(tmp_path):
    baseline = _route()
    offer = ProjectedCandidate(
        StationCandidate(
            "pending",
            "pending",
            "Road",
            "Example",
            "IL",
            Coordinates(41.05, -87.9),
            Decimal(2),
        ),
        Decimal(10000),
        Decimal(0),
        "pending",
    )

    def forbidden(*args):
        raise AssertionError("no road request fits the remaining window")

    local = SimpleNamespace(graph_version="road", max_points=2, matrix_block=forbidden)
    result = build_query_road_graph(
        baseline,
        (offer,),
        (),
        local,
        EndpointBlockCache(tmp_path, local),
        Decimal(5),
        Decimal(0),
        RequestBudget(),
        20,
    )
    assert result is not None
    assert result.unknown_ids == ("pending",)
    assert result.unavailable_ids == ()


def test_partial_refiner_queries_promising_offers_with_no_complete_rows(tmp_path):
    baseline = _route()
    start = baseline.waypoints[0].requested
    offer = ProjectedCandidate(
        StationCandidate(
            "pending", "pending", "Road", "Example", "IL", start, Decimal(2)
        ),
        Decimal(0),
        Decimal(0),
        "pending",
    )
    candidates = SimpleNamespace(
        version="csv",
        road_point_hash="points",
        stations=(offer.station,),
        along_route=lambda *args: (offer,),
        within_detour_envelope=lambda *args: (offer,),
    )
    local = SimpleNamespace(
        graph_version="road",
        max_points=2,
        matrix_block=lambda sources, targets, budget: LocalRoadBlock(
            ((Decimal(100000),),), ((Decimal(1000),),)
        ),
    )
    refiner = NationalRoadRefiner(
        tmp_path / "missing-edges.db", candidates, local, allow_partial=True
    )
    with (
        patch(
            "app.planning.national_refiner.completed_road_points", return_value=set()
        ),
        patch(
            "app.planning.national_refiner.build_trip_road_graph",
            side_effect=AssertionError(
                "pending rows cannot use complete-source builder"
            ),
        ),
    ):
        result = refiner.propose(
            start,
            baseline.waypoints[-1].requested,
            Decimal(0),
            Decimal(0),
            Decimal(100),
            Decimal(5000),
            None,
            RequestBudget(),
            baseline=baseline,
        )
    assert result is not None
    assert [v.station.station_id for v in result] == ["pending"]


def test_verified_native_seed_survives_null_optional_road_cells(tmp_path):
    baseline = _route()
    start, finish = (v.requested for v in baseline.waypoints)
    offer = ProjectedCandidate(
        StationCandidate(
            "pending", "pending", "Road", "Example", "IL", start, Decimal(2)
        ),
        Decimal(0),
        Decimal(0),
        "pending",
    )
    route = replace(
        baseline,
        waypoints=(
            RouteWaypoint(start, start, 0, Decimal(0)),
            RouteWaypoint(start, start, 0, Decimal(0)),
            RouteWaypoint(finish, finish, 1, Decimal(0)),
        ),
        legs=(RouteLeg(Decimal(0), Decimal(0)), baseline.legs[0]),
        engine_version="valhalla:road:auto",
    )
    local = SimpleNamespace(
        graph_version="road",
        max_points=2,
        matrix_block=lambda sources, targets, budget: LocalRoadBlock(
            ((None,),), ((None,),)
        ),
    )
    cache = EndpointBlockCache(tmp_path, local)
    budget = RequestBudget()
    seeded = build_query_road_graph(
        baseline,
        (offer,),
        ("pending",),
        local,
        cache,
        Decimal(0),
        Decimal(0),
        budget,
        2,
        incumbent_route=route,
    )
    assert seeded is not None
    assert search_fuel_graph(seeded.graph, Decimal(0), Decimal(0)).solution is not None
    assert seeded.unknown_ids == ()
    assert budget.metrics["local_verified_seed_edges"] > 0
    assert budget.metrics["local_reused_road_cells"] == 1
    assert budget.metrics.get("local_queried_road_cells", 0) == 0
    # Hosted-profile road legs cannot populate a native query graph.
    foreign = build_query_road_graph(
        baseline,
        (offer,),
        ("pending",),
        local,
        cache,
        Decimal(0),
        Decimal(0),
        RequestBudget(),
        2,
        incumbent_route=replace(route, engine_version="ors"),
    )
    assert foreign is not None
    assert search_fuel_graph(foreign.graph, Decimal(0), Decimal(0)).solution is None
