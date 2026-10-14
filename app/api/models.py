"""Opaque, expiring saved verified trips."""

from django.db import models


class TripResult(models.Model):
    public_id = models.CharField(max_length=64, unique=True)
    token_hash = models.CharField(max_length=64)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
