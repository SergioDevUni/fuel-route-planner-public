from django.db import models
from django.db.models import Q


class GazetteerVersion(models.Model):
    source_sha256 = models.CharField(max_length=64, unique=True)
    boundary_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=16, default="staged")
    imported_at = models.DateTimeField(auto_now_add=True)
    place_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["status"],
                condition=Q(status="active"),
                name="one_active_gazetteer",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["staged", "active", "retired"]),
                name="valid_gazetteer_status",
            ),
        ]


class Place(models.Model):
    version = models.ForeignKey(GazetteerVersion, on_delete=models.PROTECT)
    feature_id = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    state = models.CharField(max_length=2)
    latitude = models.FloatField()
    longitude = models.FloatField()
    population = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["version", "feature_id"], name="unique_place_feature"
            ),
            models.CheckConstraint(
                condition=Q(latitude__gte=-90, latitude__lte=90),
                name="place_latitude_range",
            ),
            models.CheckConstraint(
                condition=Q(longitude__gte=-180, longitude__lte=180),
                name="place_longitude_range",
            ),
        ]
        indexes = [models.Index(fields=["version", "state", "name"])]


class PlaceAlias(models.Model):
    place = models.ForeignKey(Place, on_delete=models.PROTECT)
    normalized_name = models.CharField(max_length=200)
    state = models.CharField(max_length=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["place", "normalized_name"], name="unique_place_alias"
            )
        ]
        indexes = [models.Index(fields=["state", "normalized_name"])]
