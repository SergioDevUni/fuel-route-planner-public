"""Persist internal review triggers for selected locality routing points."""

from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from app.planning.trip_types import PlannedTrip
from app.stations.models import SelectedLocationReview


def record_selected_locality_stops(trip: PlannedTrip) -> None:
    with transaction.atomic():
        for index, (station, visit) in enumerate(
            zip(trip.routed_stations, trip.fuel.visits, strict=True)
        ):
            if station.location_precision != "city_representative":
                continue
            purchased = int(visit.purchased_gallons > 0)
            snap = trip.verified.waypoints[index + 1].snap_distance_meters
            defaults = {
                "purchased_count": purchased,
                "last_snap_meters": snap.quantize(Decimal("0.001")),
                "last_purchase_gallons": visit.purchased_gallons.quantize(
                    Decimal("0.00000001")
                ),
            }
            review, created = SelectedLocationReview.objects.get_or_create(
                candidate_sha256=trip.candidate_version,
                opis_id=station.station_id,
                defaults=defaults,
            )
            if not created:
                SelectedLocationReview.objects.filter(pk=review.pk).update(
                    selected_count=F("selected_count") + 1,
                    purchased_count=F("purchased_count") + purchased,
                    last_snap_meters=defaults["last_snap_meters"],
                    last_purchase_gallons=defaults["last_purchase_gallons"],
                    last_seen_at=timezone.now(),
                )
