"""Enforce module size and forbidden dependencies; report review hotspots."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inspect(root: Path) -> tuple[list[str], list[str]]:
    errors, reviews = [], []
    for directory in ("app", "config", "scripts", "tests"):
        for path in sorted((root / directory).rglob("*.py")):
            if "migrations" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            name = path.relative_to(root).as_posix()
            lines = len(source.splitlines())
            if lines > 800:
                errors.append(f"{name}: {lines} lines exceeds 800")
            elif lines > 500:
                reviews.append(f"{name}: {lines} lines requires size review")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                imported = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else []
                )
                if name.startswith(("app/", "config/")) and any(
                    module == "scripts" or module.startswith("scripts.")
                    for module in imported
                ):
                    errors.append(f"{name}: application imports an executable tool")
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    length = (node.end_lineno or node.lineno) - node.lineno + 1
                    if length > 120:
                        reviews.append(
                            f"{name}:{node.lineno} {node.name}: {length} lines"
                        )
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if name.startswith("app/") and module.startswith(
                        "app.management.commands"
                    ):
                        errors.append(f"{name}: runtime imports a command")
                    if name.startswith(("app/", "tests/")) and module.startswith(
                        "tests.test_"
                    ):
                        errors.append(f"{name}: shared fixture imports a test module")
                    if name.startswith("scripts/benchmarking/") and module in (
                        "scripts.benchmark_nationwide_quality",
                        "scripts.benchmark_local_first_api",
                        "scripts.benchmark_matrix_selector",
                        "scripts.benchmark_release_performance",
                    ):
                        errors.append(f"{name}: shared benchmark imports a CLI")
    return errors, reviews


def main() -> None:
    errors, reviews = inspect(ROOT)
    for message in reviews:
        print("REVIEW " + message)
    for message in errors:
        print("ERROR " + message)
    if errors:
        raise SystemExit(1)
    print("Module ceiling and dependency boundaries passed.")


if __name__ == "__main__":
    main()
