from scripts.release_acceptance import acceptance


def test_unfinished_measurements_cannot_pass_release_acceptance():
    result = acceptance({"rows": []}, {"rows": []}, {"passed": True})
    assert not result["passed"]
    assert result["checks"]["deadline"]
    assert not result["checks"]["nationwide_completion"]
    assert not result["checks"]["cost_quality"]


def test_missing_evidence_is_reported_as_unmet_gates():
    result = acceptance(None, None, None)
    assert not result["passed"]
    assert not any(result["checks"].values())
