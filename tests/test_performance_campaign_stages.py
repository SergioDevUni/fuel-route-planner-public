"""Refactored benchmark stages preserve pacing, trial replacement and identities."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.benchmarking.performance_campaign import PerformanceCampaign


def campaign():
    value = PerformanceCampaign.__new__(PerformanceCampaign)
    value.args = SimpleNamespace(
        repeats=1, skip_concurrency=False, concurrency=[1], resume=False
    )
    value.names = ["los_angeles_dallas"]
    value.run_id = "fixed"
    value.report = {"rows": [], "concurrent_rows": [], "batches": []}
    value.pacing = Mock()
    value.checkpoint, value.reset = Mock(), Mock()
    value.request = Mock(
        return_value={
            "hosted_attempts": 4,
            "http_status": 200,
            "seconds": 1,
            "contract_ok": True,
        }
    )
    return value


def test_sequential_preserves_three_states_and_replaces_partial_trial():
    value = campaign()
    value.report["rows"] = [
        {"case": value.names[0], "trial": 1, "cache_state": "routing_cold"}
    ]
    value.sequential()
    assert len(value.report["rows"]) == 3
    assert len(value.report["superseded_partial_trials"]) == 1
    assert [call.args[0] for call in value.pacing.before_request.call_args_list] == [
        4,
        4,
        0,
    ]
    assert [call.args[0] for call in value.reset.call_args_list] == [
        "routing_cold",
        "routing_warm",
        "result_cached",
    ]
    assert value.current_case == value.names[0]


def test_sequential_completed_trial_is_skipped():
    value = campaign()
    value.report["rows"] = [{"case": value.names[0], "trial": 1} for _ in range(3)]
    value.sequential()
    value.request.assert_not_called()
    value.pacing.before_request.assert_not_called()


def test_concurrent_resume_keeps_completed_batches():
    value = campaign()
    value.concurrent()
    assert len(value.report["batches"]) == 3
    assert len(value.report["concurrent_rows"]) == 3
    value.request.reset_mock()
    value.concurrent()
    value.request.assert_not_called()


def test_concurrent_rejects_incomplete_batch():
    value = campaign()
    value.args.concurrency = [3]
    with pytest.raises(ValueError, match="complete concurrent batches"):
        value.concurrent()
