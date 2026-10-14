"""Independent route witnesses and conservative first-leg impossibility bounds."""

from collections import Counter
from decimal import Decimal

import numpy as np
from pyproj import Geod

from app.domain.units import METERS_PER_MILE

GEOD = Geod(ellps="WGS84")
NO_PLAN = {"TRIP_OPTIONS_REQUIRED", "NO_FEASIBLE_PLAN_IN_SEARCH_SCOPE"}


def first_leg_bound(start, finish, stations, initial, reserve):
    """Subtract allowed snaps before comparing all offers with usable inventory."""
    available = (initial - reserve) * 10 * METERS_PER_MILE
    _, _, direct = GEOD.inv(*start.as_provider_pair(), *finish.as_provider_pair())
    distances = [
        GEOD.inv(*start.as_provider_pair(), *station.coordinates.as_provider_pair())[2]
        for station in stations
    ]
    nearest = min(distances, default=float("inf"))
    # Origin/destination snaps allow 1000 m; station snaps allow 250 m.
    # A further 100 m tolerance keeps rounding and route encoding conservative.
    station_bound = max(0, nearest - 1350)
    finish_bound = max(0, direct - 2100)
    evidence = station_bound > float(available) and finish_bound > float(available)
    return {
        "status": "checked_infeasible" if evidence else "undetermined",
        "method": "all-offer geodesic first-leg lower bound with snap allowances",
        "offers_checked": len(stations),
        "usable_range_miles": str(available / METERS_PER_MILE),
        "nearest_offer_geodesic_miles": round(nearest / float(METERS_PER_MILE), 6)
        if stations
        else None,
        "nearest_offer_lower_bound_miles": round(
            station_bound / float(METERS_PER_MILE), 6
        )
        if stations
        else None,
        "finish_lower_bound_miles": round(finish_bound / float(METERS_PER_MILE), 6),
    }


def witness(route, baseline, station_ids, initial, reserve):
    """Check a capacity-refill fuel witness using only independently routed legs."""
    errors = []
    if len(route.legs) != len(station_ids) + 1:
        errors.append("leg_count")
    if route.duration_seconds > baseline.duration_seconds + 1800:
        errors.append("extra_driving_allowance")
    for index, point in enumerate(route.waypoints):
        allowance = 1000 if index in (0, len(route.waypoints) - 1) else 250
        if point.snap_distance_meters > allowance:
            errors.append("snap_allowance")
    for index, leg in enumerate(route.legs):
        inventory = initial if index == 0 else Decimal(50)
        if leg.distance_meters > (inventory - reserve) * 10 * METERS_PER_MILE:
            errors.append("fuel_range")
    return {
        "status": "checked_feasible" if not errors else "undetermined",
        "method": "independent road itinerary with capacity-refill fuel witness",
        "errors": sorted(set(errors)),
        "station_ids": list(station_ids),
        "leg_distances_meters": [str(leg.distance_meters) for leg in route.legs],
        "extra_duration_seconds": str(
            route.duration_seconds - baseline.duration_seconds
        ),
        "engine_version": route.engine_version,
        "fetched_at": route.fetched_at.isoformat(),
        "waypoints": [
            {
                "requested": {
                    "latitude": point.requested.latitude,
                    "longitude": point.requested.longitude,
                },
                "snap_distance_meters": str(point.snap_distance_meters),
            }
            for point in route.waypoints
        ],
    }


def station_chain(baseline, stations, initial, reserve, fraction=0.75):
    """Select a witness itinerary by progress along sampled direct geometry."""
    points = baseline.geometry
    step = max(1, len(points) // 600)
    samples = list(range(0, len(points), step))
    if samples[-1] != len(points) - 1:
        samples.append(len(points) - 1)
    progress = np.zeros(len(points))
    for index in range(1, len(points)):
        progress[index] = (
            progress[index - 1]
            + GEOD.inv(
                *points[index - 1].as_provider_pair(), *points[index].as_provider_pair()
            )[2]
        )
    if progress[-1] == 0:
        return ()
    progress *= float(baseline.distance_meters) / progress[-1]
    lat = np.radians([points[index].latitude for index in samples])
    lon = np.radians([points[index].longitude for index in samples])
    candidates = []
    for station in stations:
        slat, slon = np.radians(station.coordinates.as_provider_pair()[::-1])
        hav = (
            np.sin((lat - slat) / 2) ** 2
            + np.cos(lat) * np.cos(slat) * np.sin((lon - slon) / 2) ** 2
        )
        distances = 2 * 6371008.8 * np.arcsin(np.sqrt(np.clip(hav, 0, 1)))
        nearest = int(np.argmin(distances))
        offset = float(distances[nearest])
        location = float(progress[samples[nearest]])
        if offset <= 8000 and 0 < location < float(baseline.distance_meters):
            candidates.append((location, offset, station))
    current, inventory, selected = 0.0, initial, []
    finish = float(baseline.distance_meters)
    while finish - current > float((inventory - reserve) * 10 * METERS_PER_MILE):
        reach = float((inventory - reserve) * 10 * METERS_PER_MILE)
        choices = [
            item
            for item in candidates
            if current + 1000 < item[0]
            and item[0] - current + 2 * item[1] <= reach * fraction
        ]
        if not choices or len(selected) >= 40:
            return ()
        location, _, station = max(
            choices, key=lambda item: (item[0], -item[1], item[2].station_id)
        )
        selected.append(station)
        current, inventory = location, Decimal(50)
    return tuple(selected)


def classify(row, feasibility):
    expected = feasibility.get("status", "undetermined")
    if row.get("contract_ok"):
        return (
            "unexpected_failure"
            if expected == "checked_infeasible"
            else "completed_plan"
        )
    if row.get("http_status") == 422 and row.get("error") in NO_PLAN:
        if expected == "checked_infeasible":
            return "correct_infeasible_rejection"
        if expected == "undetermined":
            return "unresolved_rejection"
    return "unexpected_failure"


def outcome_summary(rows, feasibility):
    counts = Counter(classify(row, feasibility.get(row["case"], {})) for row in rows)
    feasible = [
        row
        for row in rows
        if feasibility.get(row["case"], {}).get("status") == "checked_feasible"
    ]
    completed = sum(row.get("contract_ok", False) for row in feasible)
    return {
        "requests": len(rows),
        "completed_plans": counts["completed_plan"],
        "correct_infeasible_rejections": counts["correct_infeasible_rejection"],
        "unexpected_failures": counts["unexpected_failure"],
        "unresolved_rejections": counts["unresolved_rejection"],
        "independently_feasible_requests": len(feasible),
        "completed_independently_feasible_requests": completed,
        "feasible_plan_completion_percent": round(100 * completed / len(feasible), 3)
        if feasible
        else None,
    }
