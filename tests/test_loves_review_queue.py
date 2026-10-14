import pytest

from scripts.build_loves_review_queue import build_queue


def _row(opis_id: str, name: str, state: str = "IL") -> dict[str, str]:
    return {
        "OPIS Truckstop ID": opis_id,
        "Truckstop Name": name,
        "Address": "I-80 EXIT 27",
        "City": "Atkinson",
        "State": state,
    }


def test_queue_preserves_branch_conflict_and_reviewed_identity() -> None:
    queue = build_queue(
        [
            _row("135", "LOVES TRAVEL STOP #766"),
            _row("201", "LOVES TRAVEL STOP #12"),
            _row("201", "LOVES TRAVEL STOP #13"),
            _row("900", "LOVES TRAVEL STOP #4", "ON"),
        ],
        {"135": {"store_number": "766", "evidence_url": "https://example.com"}},
    )
    assert [item["opis_id"] for item in queue] == ["135", "201"]
    assert queue[0]["review_status"] == "site_identified_access_pending_check"
    assert queue[1]["store_numbers"] == [12, 13]
    assert queue[1]["review_status"] == "ambiguous_source_branch"


def test_review_cannot_override_conflicting_source_branch() -> None:
    with pytest.raises(ValueError, match="differs from source"):
        build_queue(
            [_row("135", "LOVES TRAVEL STOP #767")],
            {"135": {"store_number": "766"}},
        )
