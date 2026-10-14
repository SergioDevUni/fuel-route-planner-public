"""Immutable routing policy, loaded only at application composition boundaries."""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field


def _flag(values: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = values.get(name, "1" if default else "0").strip()
    if raw not in ("0", "1"):
        raise ValueError(f"{name} must be 0 or 1")
    return raw == "1"


@dataclass(frozen=True, slots=True)
class PlannerSettings:
    ors_api_key: str = field(default="", repr=False, compare=False)
    planning_capacity: int = 6
    admission_wait_seconds: float = 4.0
    matrix_graph: bool = True
    matrix_first: bool = True
    matrix_cache_version: str = "v1"
    partial_refiner: bool = False
    partial_local_first: bool = False
    partial_portfolio: bool = False
    national_local_router: bool = False
    valhalla_url: str = ""
    valhalla_version: str = ""
    road_database: str = ""
    osrm_url: str = ""
    osrm_version: str = ""
    osrm_bounds: tuple[float, float, float, float] | None = None

    @classmethod
    def from_environment(cls, values: Mapping[str, str]) -> "PlannerSettings":
        capacity = int(values.get("FUEL_PLANNING_CAPACITY", "6"))
        if not 1 <= capacity <= 128:
            raise ValueError("planning capacity must be between 1 and 128")
        admission_wait = float(values.get("FUEL_ADMISSION_WAIT_SECONDS", "4"))
        if not math.isfinite(admission_wait) or not 0 <= admission_wait <= 4:
            raise ValueError("admission wait must be between 0 and 4 seconds")
        url = values.get("FUEL_LOCAL_OSRM_URL", "").strip()
        version = values.get("FUEL_LOCAL_OSRM_VERSION", "").strip()
        bounds = None
        if url:
            raw = tuple(
                float(v.strip())
                for v in values.get("FUEL_LOCAL_OSRM_BOUNDS", "").split(",")
            )
            if len(raw) != 4 or not version:
                raise ValueError("regional OSRM needs a graph version and four bounds")
            west, south, east, north = raw
            if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
                raise ValueError("regional OSRM bounds are invalid")
            bounds = west, south, east, north
        return cls(
            ors_api_key=values.get("ORS_API_KEY", "").strip(),
            planning_capacity=capacity,
            admission_wait_seconds=admission_wait,
            matrix_graph=_flag(values, "FUEL_MATRIX_GRAPH", True),
            matrix_first=_flag(values, "FUEL_MATRIX_FIRST", True),
            matrix_cache_version=values.get("FUEL_MATRIX_CACHE_VERSION", "v1"),
            partial_refiner=_flag(values, "FUEL_PARTIAL_ROAD_REFINER"),
            partial_local_first=_flag(values, "FUEL_PARTIAL_ROAD_LOCAL_FIRST"),
            partial_portfolio=_flag(values, "FUEL_PARTIAL_ROAD_PORTFOLIO"),
            national_local_router=_flag(values, "FUEL_NATIONAL_LOCAL_ROUTER"),
            valhalla_url=values.get("FUEL_NATIONAL_VALHALLA_URL", "").strip(),
            valhalla_version=values.get("FUEL_NATIONAL_VALHALLA_VERSION", "").strip(),
            road_database=values.get("FUEL_STATION_ROAD_DATABASE", "").strip(),
            osrm_url=url,
            osrm_version=version,
            osrm_bounds=bounds,
        )

    def routing_identity(self) -> dict[str, object]:
        """A secret-free identity shared by cache keys and route construction."""
        return {
            "national_road_version": self.valhalla_version,
            "national_road_url": self.valhalla_url,
            "road_database": self.road_database,
            "partial_refiner": self.partial_refiner,
            "partial_road_local_first": self.partial_local_first,
            "partial_road_portfolio": self.partial_portfolio,
            "national_local_router": self.national_local_router,
            "matrix_graph_enabled": self.matrix_graph,
            "matrix_first_enabled": self.matrix_first,
            "matrix_cache_version": self.matrix_cache_version,
            "regional_matrix": (self.osrm_url, self.osrm_version, self.osrm_bounds)
            if self.osrm_url and self.matrix_graph
            else None,
        }


def planner_settings() -> PlannerSettings:
    # Callers retain this immutable snapshot for their entire request. Explicit
    # environment changes between benchmark profiles create a new snapshot.
    return PlannerSettings.from_environment(os.environ)
