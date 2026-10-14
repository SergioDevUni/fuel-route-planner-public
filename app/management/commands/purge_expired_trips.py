"""Delete expired trip results with short bounded write transactions."""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.api.models import TripResult


class Command(BaseCommand):
    help = "Delete expired results in batches; supports --dry-run and --max-batches."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--max-batches", type=int, default=20)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        size, batches = options["batch_size"], options["max_batches"]
        if not 1 <= size <= 5000 or not 1 <= batches <= 10000:
            raise CommandError("Batch size must be 1-5000; max batches 1-10000")
        cutoff = timezone.now()
        expired = TripResult.objects.filter(expires_at__lte=cutoff)
        if options["dry_run"]:
            self.stdout.write(f"Expired results: {expired.count()} (dry run)")
            return
        removed = 0
        for _ in range(batches):
            ids = list(
                expired.order_by("expires_at", "pk").values_list("pk", flat=True)[:size]
            )
            if not ids:
                break
            count, _ = TripResult.objects.filter(
                pk__in=ids, expires_at__lte=cutoff
            ).delete()
            removed += count
        self.stdout.write(
            f"Deleted {removed} expired results; remaining: {expired.count()}"
        )
