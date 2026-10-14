import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.planning.budget import RequestBudget
from app.routing.client import OpenRouteServiceAdapter
from scripts.benchmark_budget import BenchmarkBudget, BenchmarkPaused
from scripts.benchmarking.budgeted_campaign import (
    CALL_CAP,
    select_sample,
    specification,
)
from scripts.local_common import ROOT
from scripts.prepare_budgeted_campaign import prepare


def test_subset_preserves_state_strata_regressions_and_inputs():
    source = json.loads((ROOT / "tests/fixtures/nationwide-sample.json").read_text())
    before = json.dumps(source)
    selected = select_sample(source)
    assert len(selected["cases"]) == 50
    ordinary = [c for c in selected["cases"].values() if c["stratum"] != "regression"]
    assert {c["start_state"] for c in ordinary} == set(source["states"])
    assert Counter(c["stratum"] for c in ordinary) == {
        "short": 16,
        "regional": 16,
        "long": 16,
    }
    assert all(c == source["cases"][name] for name, c in selected["cases"].items())
    assert {"oakland_fresno_10", "oakland_fresno_50"} <= selected["cases"].keys()
    assert json.dumps(source) == before
    source["cases"]["al_short_5"]["start_state"] = "NY"
    with pytest.raises(ValueError, match="sample input differs"):
        select_sample(source)


def test_both_phases_share_cap_and_preparation_preserves_old_ledger(tmp_path):
    old = tmp_path / "old-calls.json"
    old.write_text('{"limit":1300,"attempts":1282}')
    original = old.read_bytes()
    directory = tmp_path / "new"
    report = prepare(directory, ROOT / "tests/fixtures/nationwide-sample.json")
    assert old.read_bytes() == original
    assert report["conservative_total_allowance"] == 904
    assert report["reserve_attempts"] == 56
    assert report["hard_call_cap"] == CALL_CAP < 1000
    assert len(report["performance_cases"]) == 12
    assert len(report["concurrent_cases"]) == 6
    assert all(6 % users == 0 for users in report["protocol"]["hosted_concurrency"])
    for invocation in report["invocations"]:
        assert invocation[invocation.index("--call-cap") + 1] == "960"
        assert (
            invocation[invocation.index("--call-ledger") + 1] == report["call_ledger"]
        )
    assert json.loads((directory / "calls.json").read_text())["attempts"] == 0
    frozen = (directory / "campaign.json").read_bytes()
    with pytest.raises(ValueError, match="output exists"):
        prepare(directory, ROOT / "tests/fixtures/nationwide-sample.json")
    assert (directory / "campaign.json").read_bytes() == frozen


def test_shared_960_cap_counts_failed_attempts_across_restart_and_concurrency(tmp_path):
    ledger = tmp_path / "calls.json"
    ledger.write_text(
        json.dumps({"schema": 1, "limit": CALL_CAP, "attempts": 958, "calls": []})
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(500, json={}))
    ) as client:
        adapter = OpenRouteServiceAdapter("fake-test-key", client)
        with BenchmarkBudget(ledger, limit=CALL_CAP):
            with pytest.raises(RuntimeError):
                adapter._post("https://example.test", {}, RequestBudget())
        with BenchmarkBudget(ledger, limit=CALL_CAP):

            def attempt(_):
                try:
                    adapter._post("https://example.test", {}, RequestBudget())
                except BenchmarkPaused:
                    return "paused"
                except RuntimeError:
                    return "counted"

            with ThreadPoolExecutor(max_workers=6) as pool:
                outcomes = list(pool.map(attempt, range(6)))
            assert outcomes.count("counted") == 1
            assert outcomes.count("paused") == 5
    assert json.loads(ledger.read_text())["attempts"] == 960
    with pytest.raises(ValueError, match="differs"):
        BenchmarkBudget(ledger, limit=1000)


def test_allowance_matches_selected_protocol():
    sample = select_sample(
        json.loads((ROOT / "tests/fixtures/nationwide-sample.json").read_text())
    )
    report = specification(sample)
    assert report["hosted_attempt_allowances"] == {
        "nationwide_preflight": 200,
        "nationwide_measurements": 200,
        "performance_sequential": 144,
        "performance_concurrent": 216,
        "independent_references": 144,
    }


def test_preparation_rejects_a_different_candidate_before_writing(tmp_path):
    source = json.loads((ROOT / "tests/fixtures/nationwide-sample.json").read_text())
    source["candidate_sha256"] = "different"
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps(source))
    directory = tmp_path / "campaign"
    with pytest.raises(ValueError, match="sample candidate differs"):
        prepare(directory, sample)
    assert not directory.exists()


def test_new_candidate_preparation_preserves_existing_shared_allowance(tmp_path):
    ledger = tmp_path / "shared-calls.json"
    ledger.write_text('{"schema":1,"limit":960,"attempts":81,"calls":[]}')
    original = ledger.read_bytes()
    report = prepare(
        tmp_path / "new",
        ROOT / "tests/fixtures/nationwide-sample.json",
        call_ledger=ledger,
    )
    assert ledger.read_bytes() == original
    assert report["call_ledger"] == str(ledger)
    assert not (tmp_path / "new/calls.json").exists()
    ledger.write_text('{"limit":1300,"attempts":81}')
    with pytest.raises(ValueError, match="existing ledger differs"):
        prepare(
            tmp_path / "another",
            ROOT / "tests/fixtures/nationwide-sample.json",
            call_ledger=ledger,
        )
    assert not (tmp_path / "another").exists()
