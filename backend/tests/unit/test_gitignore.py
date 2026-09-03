"""Repository hygiene: secrets and local databases stay ignored, .env.example stays names-only."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GITIGNORE_TEXT = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

REQUIRED_PATTERNS = [
    ".env",
    ".env.*",
    "!.env.example",
    "*.pem",
    "*.key",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "node_modules/",
    ".venv/",
    "__pycache__/",
    "/.pytest_tmp_*/",
    "/frontend/debug.log",
    "*.log",
    "*.db-wal",
    "*.db-shm",
    "*.sqlite-journal",
    "*.sqlite-wal",
    "*.sqlite-shm",
    "*.sqlite3-journal",
    "*.sqlite3-wal",
    "*.sqlite3-shm",
    "/cloud-reference.md",
]


def test_gitignore_covers_secrets_and_local_databases() -> None:
    for pattern in REQUIRED_PATTERNS:
        assert pattern in GITIGNORE_TEXT, f".gitignore is missing pattern: {pattern}"


def test_no_local_env_file_exists_besides_the_example() -> None:
    assert not (REPO_ROOT / ".env").exists(), "a real .env file must not live in the repo root"


def test_env_example_contains_names_only() -> None:
    example = REPO_ROOT / ".env.example"
    assert example.is_file(), ".env.example must exist"
    for line in example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            name, value = stripped.split("=", 1)
            assert value.strip() == "", f".env.example must be names-only; got value on: {name}"


def _find_git() -> str | None:
    """Locate git even when the current shell carries a pre-install stale PATH."""
    found = shutil.which("git")
    if found:
        return found
    fallback = Path(r"C:\Program Files\Git\cmd\git.exe")
    if fallback.is_file():
        return str(fallback)
    return None


def test_git_check_ignore_when_git_is_available() -> None:
    git = _find_git()
    if git is None:
        pytest.skip(
            "git binary not found on PATH or at the standard install location; "
            "the pattern assertions above cover this case"
        )
    probes = [
        ".env",
        "local.db",
        "argus.local.sqlite3",
        "secrets.pem",
        "private.key",
        ".pytest_tmp_cleanup_probe/test-generated.txt",
        "frontend/debug.log",
        "backend/server.log",
        "local.db-wal",
        "local.db-shm",
        "local.sqlite-journal",
        "local.sqlite-wal",
        "local.sqlite-shm",
        "argus.local.sqlite3-journal",
        "argus.local.sqlite3-wal",
        "argus.local.sqlite3-shm",
        "cloud-reference.md",
        "tmp/local-fixture.json",
        "artifacts/raw/imports/session/manifest.json",
    ]
    for name in probes:
        result = subprocess.run(
            [git, "check-ignore", "-q", name],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, f"{name} is not ignored by git"


def test_checkpoint_sources_and_fixtures_are_not_ignored() -> None:
    git = _find_git()
    if git is None:
        pytest.skip("git is unavailable")
    for name in (
        ".env.example",
        "backend/app/importers/demo_settlement.py",
        "backend/app/persistence/gateway_imports.py",
        "frontend/src/lib/import-session-state.ts",
        "frontend/package-lock.json",
        "backend/requirements.lock.txt",
        "demo_scenarios/rzp_companions_v1/inputs/bank_entries.csv",
        "demo_scenarios/rzp_companions_v1/labels/scenario.json",
        "artifacts/evaluation/phase-07.json",
    ):
        result = subprocess.run(
            [git, "check-ignore", "--no-index", "-q", name],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1, f"Important checkpoint file is ignored: {name}"
