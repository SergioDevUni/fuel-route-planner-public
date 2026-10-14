"""Normalized name evidence shared by station identity matching."""

import re
from difflib import SequenceMatcher


def tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def name_similarity(names: list[str], candidate_name: str) -> float:
    normalized_candidate = " ".join(sorted(tokens(candidate_name)))
    return max(
        (
            SequenceMatcher(
                None, " ".join(sorted(tokens(name))), normalized_candidate
            ).ratio()
            for name in names
        ),
        default=0.0,
    )
