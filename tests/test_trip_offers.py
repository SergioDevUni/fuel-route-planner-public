from decimal import Decimal

from app.api.serialization import serialize_trip
from app.api.validation import TripInput
from app.domain.location import ResolvedLocation
from app.domain.units import METERS_PER_MILE
from app.planning.candidates import ProjectedCandidate
from app.planning.trip import TripPlanner
from tests.support.planning import (
    FINISH,
    START,
    STATIONS,
    FakeCandidates,
    FakeRouter,
    _route,
    _station,
)


def test_two_call_trip_uses_verified_legs_and_csv_prices() -> None:
    router = FakeRouter()
    trip = TripPlanner(router, FakeCandidates()).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        Decimal(20),
        Decimal("0.1"),
        Decimal("3.20"),
    )
    assert router.calls == 2
    assert [item.station_id for item in trip.routed_stations] == ["1", "2"]
    assert trip.ledger.purchased_gallons == 50
    assert trip.ledger.consumed_gallons == 100
    assert trip.fuel.ending_gallons == 0
    assert trip.outbound_attempts == 2
    request = TripInput(
        {"type": "coordinates", "latitude": 40, "longitude": -90},
        {"type": "coordinates", "latitude": 40, "longitude": -70},
        Decimal(50),
        Decimal("3.20"),
        Decimal(0),
        Decimal(20),
        Decimal("0.1"),
    )
    payload = serialize_trip(
        request,
        ResolvedLocation(START, "coordinates", "IL"),
        ResolvedLocation(FINISH, "coordinates", "PA"),
        trip,
    )
    assert payload["vehicle"]["range_miles"] == 500
    assert payload["vehicle"]["miles_per_gallon"] == 10
    assert payload["route"]["geometry"]["type"] == "LineString"
    assert len(payload["fuel_stops"]) == 2
    assert payload["costs"]["fuel_purchases_cost"] == "160.00000000"
    assert Decimal(payload["costs"]["fuel_purchases_cost"]) == sum(
        (Decimal(stop["purchase_cost"]) for stop in payload["fuel_stops"]),
        Decimal(0),
    )
    assert payload["route"]["legs"][0]["arrival_fuel_gallons"] == "10.00000000"
    assert payload["route"]["legs"][0]["departure_fuel_gallons"] == "50.00000000"
    assert payload["fuel_stops"][0]["price_per_gallon"] == "3.00000000"
    assert "location_precision" not in payload["fuel_stops"][0]
    assert payload["assumptions"]["optimization_mode"] == "balanced"
    assert payload["assumptions"]["max_extra_duration_minutes"] == "30.00000000"
    assert payload["route"]["baseline_duration_seconds"] == "100.00000000"
    assert payload["route"]["extra_duration_seconds"] == "200.00000000"


def test_shared_point_keeps_both_offers_and_buys_from_cheaper_id() -> None:
    expensive = _station("101", -82, "4")
    cheap = _station("202", -82, "3")

    class SharedCandidates(FakeCandidates):
        stations = (expensive, cheap, STATIONS[1])

        def along_route(self, route, corridor_meters=8000):
            return tuple(
                ProjectedCandidate(
                    station,
                    Decimal(400 if station.station_id != "2" else 800)
                    * METERS_PER_MILE,
                    Decimal(100),
                    f"{station.station_id}:1",
                )
                for station in self.stations
            )

    router = FakeRouter()
    trip = TripPlanner(router, SharedCandidates()).plan(
        START,
        FINISH,
        Decimal(50),
        Decimal(0),
        Decimal(20),
        Decimal("0.1"),
        Decimal("3.20"),
    )
    assert [station.station_id for station in trip.routed_stations] == ["202", "2"]
    assert [
        (visit.station_id, visit.price_per_gallon) for visit in trip.fuel.visits
    ] == [
        ("202", Decimal("3")),
        ("2", Decimal("4")),
    ]
    assert trip.ledger.exact_purchase_cost == Decimal("160")
    assert router.calls == 2


def test_national_refinement_keeps_cheaper_verified_csv_offer() -> None:
    cheap = _station("cheap", -80, "1")

    class NationalRouter(FakeRouter):
        def route(self, points, budget):
            if len(points) == 3 and points[1] == cheap.coordinates:
                budget.consume_attempt()
                self.calls += 1
                return _route(points, (400, 400))
            return super().route(points, budget)

    class NationalProposal:
        allow_partial = False

        def propose(self, *args, **kwargs):
            return (
                ProjectedCandidate(
                    cheap, Decimal(400) * METERS_PER_MILE, Decimal(0), "cheap:national"
                ),
            )

    router = NationalRouter()
    trip = TripPlanner(
        router,
        FakeCandidates(),
        national_refiner=NationalProposal(),  # type: ignore[arg-type]
    ).plan(START, FINISH, Decimal(50), Decimal(0), None, None, Decimal("3.20"))
    assert trip.selection_method == "nationwide_csv_road_refinement"
    assert [station.station_id for station in trip.routed_stations] == ["cheap"]
    assert trip.fuel.exact_purchase_cost == Decimal(30)
    assert trip.outbound_attempts == 3


def test_national_refinement_keeps_incumbent_if_actual_cost_is_higher() -> None:
    expensive = _station("expensive", -80, "10")

    class NationalRouter(FakeRouter):
        def route(self, points, budget):
            if len(points) == 3 and points[1] == expensive.coordinates:
                budget.consume_attempt()
                self.calls += 1
                return _route(points, (400, 400))
            return super().route(points, budget)

    class NationalProposal:
        def propose(self, *args, **kwargs):
            return (
                ProjectedCandidate(
                    expensive,
                    Decimal(400) * METERS_PER_MILE,
                    Decimal(0),
                    "expensive:national",
                ),
            )

    router = NationalRouter()
    trip = TripPlanner(
        router,
        FakeCandidates(),
        national_refiner=NationalProposal(),  # type: ignore[arg-type]
    ).plan(START, FINISH, Decimal(50), Decimal(0), None, None, Decimal("3.20"))
    assert trip.selection_method == "road_corridor_selection"
    assert [station.station_id for station in trip.routed_stations] == ["1", "2"]
    assert trip.fuel.exact_purchase_cost == Decimal(160)
