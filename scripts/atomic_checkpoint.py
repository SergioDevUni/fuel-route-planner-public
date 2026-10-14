"""Retry transient Windows sharing violations without abandoning a checkpoint."""

import json
import time


def write_checkpoint(path, document, **options):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, **options) + "\n", encoding="utf-8"
    )
    deadline = time.monotonic() + 2
    while True:
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)
