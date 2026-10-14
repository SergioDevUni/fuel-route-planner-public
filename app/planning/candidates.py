"""Project labelled station location points onto a route in meter units."""

import hashlib
import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property, lru_cache
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from pyproj import Geod
from shapely import box, linestrings
from shapely.strtree import STRtree

from app.domain.routing import Coordinates, Route
from app.planning.budget import RequestBudget
from app.planning.road_cache_identity import point_set_hash

WGS84 = Geod(ellps="WGS84")
StationPositions = tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]
_discovery_budget: ContextVar[tuple[RequestBudget, str] | None] = ContextVar(
    "station_discovery_budget", default=None
)


@contextmanager
def discovery_context(budget: RequestBudget, profile: str) -> Iterator[None]:
    budget.check_deadline()
    token = _discovery_budget.set((budget, profile))
    try:
        yield
    finally:
        _discovery_budget.reset(token)


def _check_discovery() -> None:
    if context := _discovery_budget.get():
        context[0].check_deadline()


def _metric(name: str, amount: int) -> None:
    if context := _discovery_budget.get():
        context[0].increment_metric(f"{context[1]}_{name}", amount)


@lru_cache(maxsize=8)
def _prepare_geometry(
    points: tuple[Coordinates, ...],
) -> tuple[STRtree, tuple[float, ...], tuple[float, ...], NDArray[np.float64]]:
    """Reuse exact segment geometry, never offers, prices or route answers."""
    _check_discovery()
    pairs = np.asarray([p.as_provider_pair() for p in points], dtype=np.float64)
    coordinates = np.column_stack((pairs[:-1], pairs[1:]))
    segments = linestrings(coordinates.reshape(-1, 2, 2))
    lengths = tuple(
        float(v)
        for v in WGS84.inv(
            coordinates[:, 0], coordinates[:, 1], coordinates[:, 2], coordinates[:, 3]
        )[2]
    )
    cumulative = [0.0]
    for length in lengths:
        cumulative.append(cumulative[-1] + length)
    coordinates.flags.writeable = False
    _metric("geometry_builds", 1)
    _check_discovery()
    return STRtree(segments), lengths, tuple(cumulative), coordinates


def _project_encounters(
    coordinates: NDArray[np.float64],
    indices: NDArray[np.int64],
    point: Coordinates,
    corridor: float,
    lengths: tuple[float, ...],
    cumulative: tuple[float, ...],
) -> list[tuple[int, float, float]]:
    """Same segment projections/grouping in numeric kernels, not fuel bins."""
    if not len(indices):
        return []
    indices = np.sort(indices)
    selected = coordinates[indices]
    east = 111_320 * math.cos(math.radians(point.latitude))
    ax = (selected[:, 0] - point.longitude) * east
    ay = (selected[:, 1] - point.latitude) * 110_574.0
    bx = (selected[:, 2] - point.longitude) * east
    by = (selected[:, 3] - point.latitude) * 110_574.0
    dx, dy = bx - ax, by - ay
    denominator = dx**2 + dy**2
    fraction = np.divide(
        -(ax * dx + ay * dy),
        denominator,
        out=np.zeros_like(denominator),
        where=denominator != 0,
    )
    fraction = np.clip(fraction, 0.0, 1.0)
    distance = np.hypot(ax + fraction * dx, ay + fraction * dy)
    keep = distance <= corridor
    indices, fraction, distance = indices[keep], fraction[keep], distance[keep]
    if not len(indices):
        return []
    boundaries = np.concatenate(
        ([0], np.flatnonzero(np.diff(indices) > 1) + 1, [len(indices)])
    )
    grouped = []
    for begin, end in zip(boundaries, boundaries[1:], strict=False):
        # First minimum preserves the scalar scan's tie order.
        winner = int(begin + np.argmin(distance[begin:end]))
        index = int(indices[winner])
        progress = cumulative[index] + float(fraction[winner]) * lengths[index]
        grouped.append((int(indices[end - 1]), progress, float(distance[winner])))
    return grouped


def _batch_encounters(
    tree: STRtree,
    coordinates: NDArray[np.float64],
    stations: tuple["StationCandidate", ...],
    corridor: float,
    lengths: tuple[float, ...],
    cumulative: tuple[float, ...],
    positions: StationPositions,
) -> Iterator[tuple["StationCandidate", list[tuple[int, float, float]]]]:
    # Bound worst-case pair output even for dense/looping route geometries.
    batch_size = max(1, min(128, 262_144 // max(1, len(coordinates))))
    for begin in range(0, len(stations), batch_size):
        _check_discovery()
        batch = stations[begin : begin + batch_size]
        lat, lon, east = (values[begin : begin + batch_size] for values in positions)
        lat_pad = corridor / 110_500
        lon_pad = corridor / np.maximum(10_000, east)
        station_indices, segment_indices = tree.query(
            box(lon - lon_pad, lat - lat_pad, lon + lon_pad, lat + lat_pad)
        )
        _metric("projection_batches", 1)
        _metric("projection_pairs", len(segment_indices))
        pieces = []
        for offset in range(0, len(segment_indices), 65_536):
            _check_discovery()
            si = station_indices[offset : offset + 65_536]
            gi = segment_indices[offset : offset + 65_536]
            selected = coordinates[gi]
            ax = (selected[:, 0] - lon[si]) * east[si]
            ay = (selected[:, 1] - lat[si]) * 110_574.0
            bx = (selected[:, 2] - lon[si]) * east[si]
            by = (selected[:, 3] - lat[si]) * 110_574.0
            dx, dy = bx - ax, by - ay
            denominator = dx**2 + dy**2
            fraction = np.divide(
                -(ax * dx + ay * dy),
                denominator,
                out=np.zeros_like(denominator),
                where=denominator != 0,
            )
            fraction = np.clip(fraction, 0.0, 1.0)
            distance = np.hypot(ax + fraction * dx, ay + fraction * dy)
            keep = distance <= corridor
            if np.any(keep):
                pieces.append((si[keep], gi[keep], fraction[keep], distance[keep]))
        if not pieces:
            continue
        si, gi, fraction, distance = (
            np.concatenate([piece[k] for piece in pieces]) for k in range(4)
        )
        order = np.lexsort((gi, si))
        si, gi, fraction, distance = (
            values[order] for values in (si, gi, fraction, distance)
        )
        boundaries = np.concatenate(([0], np.flatnonzero(np.diff(si)) + 1, [len(si)]))
        for left, right in zip(boundaries, boundaries[1:], strict=False):
            _check_discovery()
            groups = np.concatenate(
                (
                    [left],
                    np.flatnonzero(np.diff(gi[left:right]) > 1) + left + 1,
                    [right],
                )
            )
            encounters = []
            for first, last in zip(groups, groups[1:], strict=False):
                winner = int(first + np.argmin(distance[first:last]))
                index = int(gi[winner])
                progress = cumulative[index] + float(fraction[winner]) * lengths[index]
                encounters.append(
                    (int(gi[last - 1]), progress, float(distance[winner]))
                )
            yield batch[int(si[left])], encounters


@dataclass(frozen=True, slots=True)
class StationCandidate:
    station_id: str
    name: str
    address: str
    city: str
    state: str
    coordinates: Coordinates
    price_per_gallon: Decimal
    location_precision: str = "site_candidate"


@dataclass(frozen=True, slots=True)
class ProjectedCandidate:
    station: StationCandidate
    progress_meters: Decimal
    offset_meters: Decimal
    occurrence_id: str


class CandidateIndex:
    def __init__(self, folder: Path, source_hash: str) -> None:
        manifest = json.loads((folder / "manifest.json").read_text())
        data = (folder / "candidates.json").read_bytes()
        if (
            manifest["source_sha256"] != source_hash
            or hashlib.sha256(data).hexdigest() != manifest["candidate_sha256"]
        ):
            raise ValueError("candidate snapshot does not match active price data")
        raw = json.loads(data)
        if len(raw) != manifest["candidate_count"]:
            raise ValueError("candidate snapshot count differs from manifest")
        self.stations = tuple(
            StationCandidate(
                station_id=item["station_id"],
                name=item["name"],
                address=item["address"],
                city=item["city"],
                state=item["state"],
                coordinates=Coordinates(item["latitude"], item["longitude"]),
                price_per_gallon=Decimal(item["price_per_gallon"]),
                location_precision=item.get("location_precision", "site_candidate"),
            )
            for item in raw
        )
        if len({item.station_id for item in self.stations}) != len(self.stations):
            raise ValueError("candidate snapshot has duplicate station IDs")
        self.version = manifest["candidate_sha256"]
        self.road_point_hash = point_set_hash(
            station.coordinates for station in self.stations
        )

    @cached_property
    def _positions(self) -> StationPositions:
        """Snapshot-owned numeric coordinates shared by all corridor widths."""
        lat = np.asarray([s.coordinates.latitude for s in self.stations])
        lon = np.asarray([s.coordinates.longitude for s in self.stations])
        east = np.asarray([111_320 * math.cos(math.radians(float(v))) for v in lat])
        for values in (lat, lon, east):
            values.flags.writeable = False
        _metric("station_position_builds", 1)
        return lat, lon, east

    @lru_cache(maxsize=16)  # noqa: B019 - immutable snapshot and endpoint coordinates.
    def _endpoint_distances(
        self, start: Coordinates, finish: Coordinates
    ) -> tuple[NDArray[np.float64], NDArray[np.float64], float]:
        _check_discovery()
        lats, lons, _ = self._positions
        firsts = WGS84.inv(
            np.full_like(lons, start.longitude),
            np.full_like(lats, start.latitude),
            lons,
            lats,
        )[2]
        lasts = WGS84.inv(
            lons,
            lats,
            np.full_like(lons, finish.longitude),
            np.full_like(lats, finish.latitude),
        )[2]
        direct = float(
            WGS84.inv(
                start.longitude, start.latitude, finish.longitude, finish.latitude
            )[2]
        )
        firsts.flags.writeable = lasts.flags.writeable = False
        _metric("endpoint_distance_builds", 1)
        _check_discovery()
        return firsts, lasts, direct

    def within_detour_envelope(
        self, route: Route, max_total_meters: Decimal
    ) -> tuple[ProjectedCandidate, ...]:
        """Find sites on plausible alternate roads using a safe distance bound.

        Straight-line origin-to-site-to-destination distance cannot exceed a
        feasible road route. The matrix decides actual reachability later.
        """
        if max_total_meters < route.distance_meters:
            raise ValueError("detour envelope is shorter than the baseline route")
        start = route.waypoints[0].requested
        finish = route.waypoints[-1].requested
        projected: list[ProjectedCandidate] = []
        _check_discovery()
        firsts, lasts, direct = self._endpoint_distances(start, finish)
        for index, (station, first, last) in enumerate(
            zip(self.stations, firsts, lasts, strict=True)
        ):
            if index % 128 == 0:
                _check_discovery()
            first, last = float(first), float(last)
            via = first + last
            if via > float(max_total_meters) + 1:
                continue
            progress = (
                route.distance_meters * Decimal(str(first / via))
                if via > 0
                else Decimal(0)
            )
            # Lower bound on added travel relative to the direct chord. This
            # ranks candidates; the directed matrix supplies real road costs.
            offset = max(0.0, (via - direct) / 2)
            projected.append(
                ProjectedCandidate(
                    station,
                    progress,
                    Decimal(str(offset)),
                    f"{station.station_id}:envelope",
                )
            )
        return tuple(projected)

    @lru_cache(maxsize=16)  # noqa: B019 - finite cache of immutable snapshot/route inputs.
    def along_route(
        self, route: Route, corridor_meters: float = 8_000.0
    ) -> tuple[ProjectedCandidate, ...]:
        if not 0 < corridor_meters <= 160_934.4:
            raise ValueError("invalid route corridor")
        points = route.geometry
        tree, lengths, cumulative, coordinates = _prepare_geometry(points)
        if cumulative[-1] <= 0:
            return ()
        projected: list[ProjectedCandidate] = []
        _metric("projection_misses", 1)
        for station, grouped in _batch_encounters(
            tree,
            coordinates,
            self.stations,
            corridor_meters,
            lengths,
            cumulative,
            self._positions,
        ):
            for occurrence, (_, progress, offset) in enumerate(grouped, 1):
                projected.append(
                    ProjectedCandidate(
                        station=station,
                        progress_meters=route.distance_meters
                        * Decimal(str(progress / cumulative[-1])),
                        offset_meters=Decimal(str(offset)),
                        occurrence_id=f"{station.station_id}:{occurrence}",
                    )
                )
        return tuple(
            sorted(
                projected,
                key=lambda item: (
                    item.progress_meters,
                    item.offset_meters,
                    item.station.station_id,
                    item.occurrence_id,
                ),
            )
        )
