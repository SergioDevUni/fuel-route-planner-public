"""Independent price/access discovery and explicit partial-matrix references."""

from decimal import Decimal

from app.domain.fuel import FuelOpportunity
from app.planning.fuel_graph import DirectedFuelGraph


def decode_graph(data):
    def rows(values):
        return tuple(
            tuple(Decimal(v) if v is not None else None for v in row) for row in values
        )

    return DirectedFuelGraph(
        tuple(
            FuelOpportunity(v["station_id"], Decimal(v["price_per_gallon"]))
            for v in data["stations"]
        ),
        rows(data["distances_meters"]),
        data["version"],
        rows(data["durations_seconds"]) if data["durations_seconds"] else None,
    )


def reference_batches(alternatives, distance, seed_ids, batches=2, limit=57):
    """Discover from all projected CSV offers without the production selector."""
    by_id = {v.station.station_id: v for v in alternatives}
    seeds = [by_id[v] for v in seed_ids if v in by_id]
    bins = [[] for _ in range(12)]
    for item in by_id.values():
        index = min(11, max(0, int(item.progress_meters / distance * 12)))
        bins[index].append(item)
    result = []
    for policy in range(batches):
        chosen = {v.station.station_id: v for v in seeds}
        points = {v.station.coordinates for v in seeds}
        queues = [
            iter(
                sorted(
                    group,
                    key=lambda v: (
                        (v.station.price_per_gallon, v.offset_meters)
                        if policy == 0
                        else (v.offset_meters, v.station.price_per_gallon)
                    ),
                )
            )
            for group in bins
            if group
        ]
        while queues and len(points) < limit:
            active = []
            for queue in queues:
                item = next(queue, None)
                if item is None:
                    continue
                active.append(queue)
                point = item.station.coordinates
                if point not in points and len(points) >= limit:
                    continue
                chosen[item.station.station_id] = item
                points.add(point)
            queues = active
        result.append(tuple(chosen.values()))
    return tuple(result)


def merge_reference_graphs(graphs):
    """Union measured directed edges; missing cross-batch edges stay unknown."""
    offers = {v.station_id: v for graph in graphs for v in graph.stations}
    stations = tuple(offers.values())
    ids = {v.station_id: i for i, v in enumerate(stations, 1)}
    size = len(stations) + 2
    distances = [[None] * size for _ in range(size)]
    durations = [[None] * size for _ in range(size)]
    for i in range(size):
        distances[i][i] = durations[i][i] = Decimal(0)
    for graph in graphs:
        if graph.durations_seconds is None:
            raise ValueError("reference needs measured directed duration data")
        indices = (0, *(ids[v.station_id] for v in graph.stations), size - 1)
        for source, target_source in enumerate(indices):
            for target, target_target in enumerate(indices):
                distance = graph.distances_meters[source][target]
                duration = graph.durations_seconds[source][target]
                if distance is None or duration is None:
                    continue
                distances[target_source][target_target] = distance
                durations[target_source][target_target] = duration
    return DirectedFuelGraph(
        stations,
        tuple(tuple(v) for v in distances),
        "independent-price-access-matrix-union",
        tuple(tuple(v) for v in durations),
    )
