"""Mechanical Phase 3 scope guards.

Phase 3 may persist proof artifacts and DRAFT correction previews, but it
must not introduce authority/application paths or write simulated ledger
entries. These checks are intentionally syntactic so the gate catches scope
creep before runtime behavior can depend on it.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "backend" / "app"
RUNTIME_PURE_ROOTS = (
    APP_ROOT / "verifier",
    APP_ROOT / "corrections",
)

FORBIDDEN_FUNCTION_PREFIXES = (
    "approve",
    "apply_correction",
    "simulate_apply",
    "apply_ledger",
    "update_ledger",
    "mark_resolved",
)


def _py_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_no_phase4_financial_action_callables_exist() -> None:
    violations: list[str] = []
    for path in _py_files(APP_ROOT):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            lowered = node.name.lower()
            if any(lowered.startswith(prefix) for prefix in FORBIDDEN_FUNCTION_PREFIXES):
                violations.append(f"{_relative(path)} defines {node.name}()")
    assert violations == []


def test_verifier_and_corrections_do_not_import_persistence() -> None:
    violations: list[str] = []
    for root in RUNTIME_PURE_ROOTS:
        for path in _py_files(root):
            for node in ast.walk(_tree(path)):
                modules: list[str]
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                if any(
                    module == "app.persistence" or module.startswith("app.persistence.")
                    for module in modules
                ):
                    violations.append(f"{_relative(path)} imports {modules}")
    assert violations == []


def test_no_simulated_correction_ledger_entry_is_constructed() -> None:
    violations: list[str] = []
    for path in _py_files(APP_ROOT):
        tree = _tree(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_ledger_constructor = (
                isinstance(func, ast.Name)
                and func.id == "LedgerEntryRecord"
                or isinstance(func, ast.Attribute)
                and func.attr == "LedgerEntryRecord"
            )
            if not is_ledger_constructor:
                continue
            for keyword in node.keywords:
                if keyword.arg != "entry_origin":
                    continue
                value = ast.unparse(keyword.value)
                if "SIMULATED_CORRECTION" in value:
                    violations.append(f"{_relative(path)} constructs simulated LedgerEntryRecord")
    assert violations == []
