import json
import subprocess
import sys

import pytest

from scripts.benchmarking.fingerprints import benchmark_sources
from scripts.local_common import ROOT
from scripts.prepare_acceptance_campaign import prepare
from scripts.release_acceptance import acceptance


@pytest.mark.integration
def test_shared_case_data_has_no_django_initialization():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from scripts.benchmarking.cases import BENCHMARK_CASES; "
            "from django.conf import settings; "
            "assert not settings.configured; assert len(BENCHMARK_CASES)==54",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_harness_identity_includes_extracted_owners():
    sources = benchmark_sources(ROOT)
    assert "scripts/benchmarking/cases.py" in sources
    assert "scripts/benchmarking/http_measurement.py" in sources
    assert "scripts/benchmarking/performance_references.py" in sources
    assert "scripts/benchmarking/performance_reporting.py" in sources


def test_campaign_preparation_does_not_authorize_execution():
    sample = json.loads((ROOT / "tests/fixtures/nationwide-sample.json").read_text())
    report = prepare(sample, "sample", "new-code", {"harness": "new"})
    assert not report["hosted_execution_authorized"]
    assert report["status"] == "awaiting_campaign"
    assert report["conservative_total_allowance"] > 18


def test_acceptance_rejects_historical_code_and_missing_platform_evidence():
    result = acceptance(
        {"planner_sha256": "old"},
        {"planner_sha256": "old"},
        {"planner_sha256": "old", "passed": True},
        expected_planner="current",
    )
    assert not result["checks"]["code_identity"]
    assert not result["checks"]["restore_installs"]
    assert not result["passed"]
