import json
from pathlib import Path

from scripts.atomic_checkpoint import write_checkpoint


def test_checkpoint_survives_transient_sharing_violation(tmp_path, monkeypatch):
    original = Path.replace
    attempts = []

    def temporarily_locked(path, target):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError("reader has not released its Windows handle")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", temporarily_locked)
    target = tmp_path / "checkpoint.json"
    write_checkpoint(target, {"completed": 42})
    assert json.loads(target.read_text()) == {"completed": 42}
    assert len(attempts) == 3
