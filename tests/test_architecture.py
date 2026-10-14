"""Enforce the small pure-core dependency boundary without extra tooling."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PURE_FILES = [
    *sorted((ROOT / "app/domain").glob("*.py")),
    ROOT / "app/planning/fuel.py",
]
FORBIDDEN_ROOTS = {"django", "httpx", "shapely", "pyproj", "config"}


def test_pure_core_has_no_framework_or_adapter_imports() -> None:
    for path in PURE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not {
            name for name in imported if name.split(".")[0] in FORBIDDEN_ROOTS
        }, path


def test_application_boundary_rejects_both_executable_tool_import_forms(tmp_path):
    from scripts.check_module_boundaries import inspect

    for folder, source in (
        ("app", "from scripts.prepare_locations import main\n"),
        ("config", "import scripts.setup_local as setup\n"),
    ):
        target = tmp_path / folder
        target.mkdir()
        (target / "bad.py").write_text(source)
    errors, _ = inspect(tmp_path)
    assert len(errors) == 2
    assert all("application imports an executable tool" in error for error in errors)
