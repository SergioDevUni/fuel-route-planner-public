"""Use a pinned regional OSRM graph when it covers every matrix point."""

import logging
from decimal import Decimal

from app.domain.road import RoadDistanceMatrix
from app.domain.routing import Coordinates
from app.planning.budget import RequestBudget
from app.planning.ports import RoadMatrixPort
from app.routing.local_osrm import LocalOSRM, LocalRoadSnapFailed, LocalRoadUnavailable

logger = logging.getLogger(__name__)


class RegionalRoadMatrixProvider:
    def __init__(
        self,
        local: LocalOSRM,
        hosted: RoadMatrixPort,
        bounds: tuple[float, float, float, float],
    ) -> None:
        west, south, east, north = bounds
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("regional road bounds are invalid")
        self.local = local
        self.hosted = hosted
        self.bounds = bounds

    def matrix(
        self, points: tuple[Coordinates, ...], budget: RequestBudget
    ) -> RoadDistanceMatrix:
        west, south, east, north = self.bounds
        if all(
            west <= point.longitude <= east and south <= point.latitude <= north
            for point in points
        ):
            try:
                budget.check_deadline()
                result = self.local.matrix(points)
                budget.check_deadline()
                if not result.durations_seconds or not result.destination_snap_meters:
                    raise LocalRoadUnavailable("regional matrix lacks route metrics")
                logger.info(
                    "local_matrix_used graph=%s points=%d",
                    result.graph_version,
                    len(points),
                )
                return RoadDistanceMatrix(
                    tuple(
                        tuple(
                            Decimal(str(value)) if value is not None else None
                            for value in row
                        )
                        for row in result.distances_meters
                    ),
                    tuple(Decimal(str(value)) for value in result.snap_meters),
                    tuple(
                        Decimal(str(value)) for value in result.destination_snap_meters
                    ),
                    tuple(
                        tuple(
                            Decimal(str(value)) if value is not None else None
                            for value in row
                        )
                        for row in result.durations_seconds
                    ),
                )
            except (LocalRoadUnavailable, LocalRoadSnapFailed) as error:
                logger.info("local_matrix_fallback reason=%s", type(error).__name__)
        return self.hosted.matrix(points, budget)
