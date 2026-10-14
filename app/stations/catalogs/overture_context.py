"""One state index and its ordered matching passes."""

from dataclasses import dataclass, field


@dataclass
class OvertureContext:
    source: dict
    eligible_ids: set[str]
    state: str
    records: list[dict]
    centers: dict
    occupied_sites: dict
    index: dict
    branch_index: dict
    branch_state_index: dict
    name_index: dict
    city_places: dict
    state_name_count: dict
    already_matched: set[str] = field(default_factory=set)
    used_places: set[str] = field(default_factory=set)
    csv_city_counts: dict = field(default_factory=dict)
