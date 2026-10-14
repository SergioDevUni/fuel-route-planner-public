"""Graph bounds for directed fuel planning."""

import heapq
from decimal import Decimal


def remaining_distances(
    matrix: tuple[tuple[Decimal | None, ...], ...],
) -> tuple[Decimal | None, ...]:
    rows = tuple(
        tuple(
            (target, length)
            for target, length in enumerate(row)
            if length is not None and source != target
        )
        for source, row in enumerate(matrix)
    )
    return remaining_from_rows(rows)


def remaining_from_rows(
    rows: tuple[tuple[tuple[int, Decimal], ...], ...],
) -> tuple[Decimal | None, ...]:
    reverse: list[list[tuple[int, Decimal]]] = [[] for _ in rows]
    for source, row in enumerate(rows):
        for target, length in row:
            reverse[target].append((source, length))
    return shortest_from_rows(
        tuple(tuple(row) for row in reverse), len(rows) - 1, Decimal("Infinity")
    )


def shortest_from_rows(
    rows: tuple[tuple[tuple[int, Decimal], ...], ...],
    source: int,
    limit: Decimal,
) -> tuple[Decimal | None, ...]:
    """Bounded directed Dijkstra for a time-feasible future-price index."""
    distances: list[Decimal | None] = [None] * len(rows)
    distances[source] = Decimal(0)
    heap = [(Decimal(0), source)]
    while heap:
        elapsed, node = heapq.heappop(heap)
        if elapsed != distances[node]:
            continue
        if elapsed > limit:
            break
        for target, seconds in rows[node]:
            candidate = elapsed + seconds
            previous = distances[target]
            if candidate <= limit and (previous is None or candidate < previous):
                distances[target] = candidate
                heapq.heappush(heap, (candidate, target))
    return tuple(distances)


def all_pairs_lower_bounds(
    matrix: tuple[tuple[Decimal | None, ...], ...],
) -> tuple[tuple[Decimal | None, ...], ...]:
    """Optimistic directed costs, allowing paths through any graph node."""
    size = len(matrix)
    edges = sum(value is not None for row in matrix for value in row)
    if size > 60 and edges < size * size // 4:
        adjacent = [
            tuple(
                (target, length)
                for target, length in enumerate(row)
                if length is not None and target != source
            )
            for source, row in enumerate(matrix)
        ]
        return tuple(
            shortest_from_rows(tuple(adjacent), source, Decimal("Infinity"))
            for source in range(size)
        )
    result = [list(row) for row in matrix]
    for node in range(len(result)):
        result[node][node] = Decimal(0)
    for middle in range(len(result)):
        for source in range(len(result)):
            before = result[source][middle]
            if before is None:
                continue
            for target in range(len(result)):
                after = result[middle][target]
                if after is None:
                    continue
                candidate = before + after
                previous = result[source][target]
                if previous is None or candidate < previous:
                    result[source][target] = candidate
    return tuple(tuple(row) for row in result)
