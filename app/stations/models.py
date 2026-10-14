from django.db import models
from django.db.models import Q


class DatasetVersion(models.Model):
    source_sha256 = models.CharField(max_length=64)
    source_filename = models.CharField(max_length=255)
    normalization_policy = models.CharField(max_length=64)
    coordinate_release = models.CharField(max_length=64, default="unresolved")
    status = models.CharField(max_length=16, default="staged")
    imported_at = models.DateTimeField(auto_now_add=True)
    counts = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_sha256", "normalization_policy", "coordinate_release"],
                name="unique_source_policy_coordinates",
            ),
            models.UniqueConstraint(
                fields=["status"],
                condition=Q(status="active"),
                name="one_active_dataset",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["staged", "active", "retired"]),
                name="valid_dataset_status",
            ),
        ]


class SourcePriceRow(models.Model):
    dataset = models.ForeignKey(DatasetVersion, on_delete=models.PROTECT)
    source_row_number = models.PositiveIntegerField()
    raw_fields = models.JSONField()
    parsed_price_text = models.CharField(max_length=32, blank=True)
    classification = models.CharField(max_length=32)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "source_row_number"], name="unique_source_row"
            )
        ]


class Station(models.Model):
    dataset = models.ForeignKey(DatasetVersion, on_delete=models.PROTECT)
    opis_id = models.CharField(max_length=64)
    display_name = models.CharField(max_length=255)
    aliases = models.JSONField(default=list)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=255)
    state = models.CharField(max_length=2)
    country_code = models.CharField(max_length=2, default="US")
    rack_id = models.CharField(max_length=64)
    selected_price = models.DecimalField(max_digits=16, decimal_places=8)
    selected_price_text = models.CharField(max_length=32)
    minimum_price_text = models.CharField(max_length=32)
    maximum_price_text = models.CharField(max_length=32)
    price_conflict = models.BooleanField(default=False)
    source_row_numbers = models.JSONField(default=list)
    selected_price_row_numbers = models.JSONField(default=list)
    location_status = models.CharField(max_length=16, default="unresolved")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "opis_id"], name="unique_dataset_opis_id"
            ),
            models.CheckConstraint(
                condition=Q(selected_price__gt=0), name="positive_selected_price"
            ),
            models.CheckConstraint(
                condition=Q(country_code__in=["US", "CA"]),
                name="supported_station_country",
            ),
        ]
        indexes = [models.Index(fields=["dataset", "country_code", "state", "opis_id"])]


class SelectedLocationReview(models.Model):
    """Internal follow-up for selected stops whose point is a locality."""

    candidate_sha256 = models.CharField(max_length=64)
    opis_id = models.CharField(max_length=64)
    selected_count = models.PositiveIntegerField(default=1)
    purchased_count = models.PositiveIntegerField(default=0)
    last_snap_meters = models.DecimalField(max_digits=12, decimal_places=3)
    last_purchase_gallons = models.DecimalField(max_digits=16, decimal_places=8)
    last_seen_at = models.DateTimeField(auto_now=True)
    review_status = models.CharField(max_length=24, default="pending")
    review_note = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["candidate_sha256", "opis_id"],
                name="unique_selected_location_review",
            ),
            models.CheckConstraint(
                condition=Q(
                    review_status__in=["pending", "location_only", "site_confirmed"]
                ),
                name="valid_selected_location_review_status",
            ),
        ]
