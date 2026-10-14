"""Aggregate validated evidence in the established source precedence order."""

from pathlib import Path

from app.stations.review_catalogs import load_epa, load_operator_catalogs
from app.stations.review_context import ReviewContext
from app.stations.review_localities import load_fallback_sites, load_reviewed_localities
from app.stations.review_operators import (
    load_green_river,
    load_loves,
    load_pilot_ta,
    load_public,
    load_regional,
)
from app.stations.review_overture import load_exact


def load_reviews(
    root: Path, source_hash: str
) -> tuple[dict[str, dict], set[str], dict]:
    context = ReviewContext(root, source_hash)
    load_regional(context)
    load_loves(context)
    load_green_river(context)
    load_pilot_ta(context)
    load_public(context)
    load_exact(context)
    load_operator_catalogs(context)
    load_reviewed_localities(context)
    load_epa(context)
    load_fallback_sites(context)
    return context.reviews, context.verified_ids, context.input_hashes
