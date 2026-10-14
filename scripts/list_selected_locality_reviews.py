"""List internally queued locality stops for site/access investigation."""

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from app.stations.models import SelectedLocationReview  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-versions", action="store_true")
    parser.add_argument("--all-statuses", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "var/station_candidates/manifest.json").read_text())
    candidates = {
        item["station_id"]: item
        for item in json.loads(
            (ROOT / "var/station_candidates/candidates.json").read_text()
        )
    }
    reviews = SelectedLocationReview.objects.all()
    if not args.all_versions:
        reviews = reviews.filter(candidate_sha256=manifest["candidate_sha256"])
    if not args.all_statuses:
        reviews = reviews.filter(review_status="pending")
    for review in reviews.order_by("-purchased_count", "-selected_count", "opis_id"):
        station = candidates.get(review.opis_id)
        print(
            json.dumps(
                {
                    "opis_id": review.opis_id,
                    "candidate_sha256": review.candidate_sha256,
                    "name": station["name"] if station else None,
                    "csv_address": station["address"] if station else None,
                    "city": station["city"] if station else None,
                    "state": station["state"] if station else None,
                    "selected_count": review.selected_count,
                    "purchased_count": review.purchased_count,
                    "last_snap_meters": str(review.last_snap_meters),
                    "last_purchase_gallons": str(review.last_purchase_gallons),
                    "last_seen_at": review.last_seen_at.isoformat(),
                    "review_status": review.review_status,
                    "review_note": review.review_note,
                    "reviewed_at": (
                        review.reviewed_at.isoformat() if review.reviewed_at else None
                    ),
                }
            )
        )


if __name__ == "__main__":
    main()
