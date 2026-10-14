"""State shared by ordered station evidence loaders."""

from dataclasses import dataclass, field
from pathlib import Path

REGIONS = ("chicago", "imperial_ca")


@dataclass
class ReviewContext:
    root: Path
    source_hash: str
    reviews: dict[str, dict] = field(default_factory=dict)
    verified_ids: set[str] = field(default_factory=set)
    input_hashes: dict[str, dict] = field(default_factory=dict)
