"""Label firewall: runtime code must not be able to reach ground truth.

Five mechanical checks (PRD 6.13, 14.1; AGENTS.md rule 6):

1. Import graph - no module under the runtime roots may import
   ``app.evaluation`` (the only package permitted to touch label data),
   including relative imports.
2. Path and field literals - no runtime source string may name a label path
   (``datasets/**/labels``), the evaluation package, or a label-only field.
3. Input purity - runtime input files must not contain label-only fields.
4. Physical layout - label content exists only under ``datasets/*/labels/``
   with exactly ``labels.json`` and ``manifest.json``; ``inputs/`` holds only
   the five CSVs; ``datasets/holdout`` holds only ``spec.json``.
5. Frontend - no dataset-label path references in ``frontend/src``.

The checker is deliberately strict; relaxing it is a conscious decision that
must be recorded, never an accident.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

RUNTIME_PY_ROOTS = (
    "backend/app/main.py",
    "backend/app/config.py",
    "backend/app/api",
    "backend/app/domain",
    "backend/app/persistence",
    "backend/app/importers",
    "backend/app/reconciliation",
    "backend/app/graph",
    "backend/app/runs.py",
)

LABEL_FIELD_NAMES = (
    "expected_category",
    "expected_outcome",
    "expected_delta_paise",
    "expected_evidence_ids",
    "must_escalate",
    "authoring_notes",
    "labels_sha256",
    "label_schema_version",
    "clean_reference",
    "row_expectations",
)

EVALUATION_MODULE_RE = re.compile(r"\bapp\.evaluation\b")

# Backend runtime: any "labels" path segment is suspect.
LABEL_PATH_RE = re.compile(r"(?:^|[\"'`/\\])labels(?:[\"'`/\\]|$)")

# Frontend: only full dataset label paths and label field names are suspect
# (the word "labels" alone is legitimate UI vocabulary).
FRONTEND_LABEL_PATH_RE = re.compile(r"datasets[\\/][a-z0-9-]+[\\/]labels")

INPUT_FILE_NAMES = tuple(
    sorted(
        f"{name}.csv"
        for name in (
            "payments",
            "refunds",
            "settlements",
            "bank_entries",
            "ledger_entries",
        )
    )
)


def _iter_files(root: Path, suffix: str) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix == suffix else []
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob(f"*{suffix}") if path.is_file())


def _relative(repo_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def collect_import_violations(repo_root: Path, extra_root: Path | None = None) -> list[str]:
    """AST scan: runtime modules must not import the evaluation package."""
    violations: list[str] = []
    roots = [repo_root / relative for relative in RUNTIME_PY_ROOTS]
    if extra_root is not None:
        roots.append(extra_root)
    for root in roots:
        for path in _iter_files(root, ".py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                for module in modules:
                    parts = module.split(".")
                    if "evaluation" in parts:
                        violations.append(
                            f"{_relative(repo_root, path)} imports evaluation "
                            f"package via '{module}'"
                        )
    return violations


def collect_literal_violations(repo_root: Path, extra_root: Path | None = None) -> list[str]:
    """Runtime source must not name label paths, evaluation modules, or label fields."""
    violations: list[str] = []
    roots = [repo_root / relative for relative in RUNTIME_PY_ROOTS]
    if extra_root is not None:
        roots.append(extra_root)
    for root in roots:
        for path in _iter_files(root, ".py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                text = node.value
                if EVALUATION_MODULE_RE.search(text):
                    violations.append(
                        f"{_relative(repo_root, path)} references app.evaluation in a "
                        "string literal"
                    )
                if LABEL_PATH_RE.search(text):
                    violations.append(
                        f"{_relative(repo_root, path)} references a labels path segment in {text!r}"
                    )
                if text in LABEL_FIELD_NAMES:
                    violations.append(
                        f"{_relative(repo_root, path)} uses label-only field name {text!r}"
                    )
    return violations


def collect_input_purity_violations(
    repo_root: Path, datasets_root: Path | None = None
) -> list[str]:
    """Input files must be pure CSVs without label-only fields."""
    violations: list[str] = []
    root = datasets_root if datasets_root is not None else repo_root / "datasets"
    if not root.is_dir():
        return []
    for profile_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        inputs = profile_dir / "inputs"
        if not inputs.is_dir():
            continue
        for path in sorted(inputs.iterdir()):
            if not path.is_file():
                continue
            if path.name not in INPUT_FILE_NAMES:
                violations.append(f"unexpected file in inputs: {_relative(repo_root, path)}")
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for field_name in LABEL_FIELD_NAMES:
                if field_name in text:
                    violations.append(
                        f"input file {_relative(repo_root, path)} contains label field {field_name}"
                    )
    return violations


def collect_physical_layout_violations(repo_root: Path) -> list[str]:
    """Labels live only under datasets/<profile>/labels with the exact names."""
    violations: list[str] = []
    root = repo_root / "datasets"
    if not root.is_dir():
        return ["datasets directory missing"]
    profile_dirs = sorted(path for path in root.iterdir() if path.is_dir())
    if not profile_dirs:
        return ["datasets directory contains no profiles"]
    for profile_dir in profile_dirs:
        profile = profile_dir.name
        if profile == "holdout":
            entries = sorted(p.name for p in profile_dir.iterdir())
            if entries != ["spec.json"]:
                violations.append(f"datasets/holdout must contain only spec.json, found {entries}")
            continue
        inputs = sorted(p.name for p in (profile_dir / "inputs").iterdir())
        if tuple(inputs) != INPUT_FILE_NAMES:
            violations.append(f"datasets/{profile}/inputs has unexpected contents: {inputs}")
        labels = sorted(p.name for p in (profile_dir / "labels").iterdir())
        if labels != ["labels.json", "manifest.json"]:
            violations.append(
                f"datasets/{profile}/labels must contain exactly labels.json and "
                f"manifest.json, found {labels}"
            )
        if not (profile_dir / "manifest.json").is_file():
            violations.append(f"datasets/{profile}/manifest.json missing")
    for path in sorted(root.rglob("labels.json")):
        if path.parent.name != "labels":
            violations.append(
                f"labels.json outside a labels directory: {_relative(repo_root, path)}"
            )
    return violations


def collect_frontend_violations(repo_root: Path) -> list[str]:
    """Frontend source must not reference dataset label paths or label fields."""
    violations: list[str] = []
    root = repo_root / "frontend" / "src"
    for path in _iter_files(root, ".ts") + _iter_files(root, ".tsx"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if FRONTEND_LABEL_PATH_RE.search(text):
            violations.append(f"{_relative(repo_root, path)} references a dataset labels path")
        for field_name in LABEL_FIELD_NAMES:
            if field_name in text:
                violations.append(
                    f"{_relative(repo_root, path)} uses label-only field name {field_name!r}"
                )
    return violations


def run_all_checks(
    repo_root: Path,
    *,
    extra_runtime_root: Path | None = None,
    datasets_root: Path | None = None,
) -> list[str]:
    """Run every firewall check; an empty list means the firewall holds."""
    violations: list[str] = []
    violations.extend(collect_import_violations(repo_root, extra_runtime_root))
    violations.extend(collect_literal_violations(repo_root, extra_runtime_root))
    if datasets_root is None:
        violations.extend(collect_input_purity_violations(repo_root))
        violations.extend(collect_physical_layout_violations(repo_root))
    else:
        violations.extend(collect_input_purity_violations(repo_root, datasets_root))
    violations.extend(collect_frontend_violations(repo_root))
    return violations
