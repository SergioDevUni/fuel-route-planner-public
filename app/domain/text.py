"""Shared comparison normalization; display text stays source-derived."""

import unicodedata


def canonical_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
