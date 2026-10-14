from decimal import Decimal
from types import SimpleNamespace

from app.domain.fuel import FuelOpportunity
from app.planning.fuel_graph import DirectedFuelGraph
from scripts.reference_scope import merge_reference_graphs, reference_batches


def test_partial_union_never_invents_cross_batch_road_edges():
    def graph(station_id):
        rows = (
            (Decimal(0), Decimal(10), Decimal(20)),
            (None, Decimal(0), Decimal(10)),
            (None, None, Decimal(0)),
        )
        return DirectedFuelGraph(
            (FuelOpportunity(station_id, Decimal(3)),), rows, "test", rows
        )

    union = merge_reference_graphs((graph("one"), graph("two")))
    assert len(union.stations) == 2
    assert union.distances_meters[1][2] is None
    assert union.distances_meters[2][1] is None
    assert union.distances_meters[0][2] == 10


def test_independent_discovery_preserves_bridges_and_distinct_shared_offers():
    candidates = tuple(
        SimpleNamespace(
            station=SimpleNamespace(
                station_id=str(i), coordinates=i // 2, price_per_gallon=Decimal(100 - i)
            ),
            progress_meters=Decimal(i),
            offset_meters=Decimal(i),
        )
        for i in range(100)
    )
    batches = reference_batches(candidates, Decimal(100), ["0", "99"], limit=20)
    for batch in batches:
        ids = {v.station.station_id for v in batch}
        assert {"0", "99"} <= ids
        assert len({v.station.coordinates for v in batch}) <= 20
    assert {v.station.station_id for v in batches[0]} != {
        v.station.station_id for v in batches[1]
    }
