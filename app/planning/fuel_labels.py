"""Fuel labels for directed fuel planning."""

from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal

from app.planning.fuel_profile import FuelCostProfile


@dataclass(slots=True)
class FuelLabel:
    node: int
    path: tuple[int, ...]
    legs: tuple[Decimal, ...]
    traveled: Decimal
    elapsed: Decimal
    profile: FuelCostProfile
    visited: int
    ids: tuple[str, ...]
    active: bool = True

    def dominates(self, other: "FuelLabel") -> bool:
        # The dominated suffix must remain available, with no more time or
        # distance consumed. Preserve the public deterministic tie order.
        return (
            self.elapsed <= other.elapsed
            and self.traveled <= other.traveled
            and self.visited & other.visited == self.visited
            and (
                self.traveled < other.traveled
                or (len(self.path), self.ids) <= (len(other.path), other.ids)
            )
            and self.profile.covers(other.profile)
        )


@dataclass(slots=True)
class _SetNode:
    children: dict[int, "_SetNode"] = field(default_factory=dict)
    labels: list[FuelLabel] = field(default_factory=list)
    min_elapsed: Decimal = Decimal("Infinity")
    max_elapsed: Decimal = Decimal("-Infinity")
    min_traveled: Decimal = Decimal("Infinity")
    max_traveled: Decimal = Decimal("-Infinity")

    def include(self, label: FuelLabel) -> None:
        self.min_elapsed = min(self.min_elapsed, label.elapsed)
        self.max_elapsed = max(self.max_elapsed, label.elapsed)
        self.min_traveled = min(self.min_traveled, label.traveled)
        self.max_traveled = max(self.max_traveled, label.traveled)


class LabelFrontier:
    """Exact subset/superset queries before expensive curve comparisons."""

    def __init__(self, initial: FuelLabel | None = None) -> None:
        self.root = _SetNode()
        if initial is not None:
            self.add(initial)

    def add(self, label: FuelLabel) -> None:
        node, bits = self.root, label.visited
        node.include(label)
        while bits:
            bit = bits & -bits
            child = node.children.get(bit)
            if child is None:
                child = _SetNode()
                node.children[bit] = child
            node = child
            node.include(label)
            bits ^= bit
        position = bisect_right(node.labels, label.elapsed, key=lambda v: v.elapsed)
        node.labels.insert(position, label)

    def eligible(self, label: FuelLabel, *, subsets: bool) -> Iterator[FuelLabel]:
        stack = [(self.root, label.visited)]
        while stack:
            node, required = stack.pop()
            # Bounds cover all descendants. After removals they may be loose,
            # which only costs work; they can never exclude an eligible label.
            if subsets:
                if (
                    node.min_elapsed > label.elapsed
                    or node.min_traveled > label.traveled
                ):
                    continue
            elif node.max_elapsed < label.elapsed or node.max_traveled < label.traveled:
                continue
            if subsets or not required:
                if subsets:
                    end = bisect_right(
                        node.labels, label.elapsed, key=lambda v: v.elapsed
                    )
                    yield from node.labels[:end]
                else:
                    begin = bisect_left(
                        node.labels, label.elapsed, key=lambda v: v.elapsed
                    )
                    yield from node.labels[begin:]
            for bit, child in node.children.items():
                if subsets:
                    if bit & label.visited:
                        stack.append((child, required))
                elif not required or bit <= required & -required:
                    stack.append((child, required & ~bit))

    def remove(self, label: FuelLabel) -> None:
        node, bits = self.root, label.visited
        ancestors = []
        while bits:
            bit = bits & -bits
            ancestors.append((node, bit))
            node = node.children[bit]
            bits ^= bit
        node.labels.remove(label)
        for parent, bit in reversed(ancestors):
            child = parent.children[bit]
            if child.labels or child.children:
                break
            del parent.children[bit]

    def accept(self, label: FuelLabel) -> tuple[bool, int]:
        if any(other.dominates(label) for other in self.eligible(label, subsets=True)):
            return False, 1
        removed = [
            other
            for other in self.eligible(label, subsets=False)
            if label.dominates(other)
        ]
        for other in removed:
            other.active = False
            self.remove(other)
        self.add(label)
        return True, len(removed)


HeapEntry = tuple[Decimal, Decimal, tuple[str, ...], int, FuelLabel]
