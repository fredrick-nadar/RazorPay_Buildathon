"""Phase 8 release-gate logic: it must never certify uncommitted or fake work.

These tests import ``scripts/verify_phase.py`` as a standalone module and
exercise its release steps directly, with the repository root redirected at
temporary directories. They never run the real gate against the real
repository and never write into the repository's ``artifacts/``.

Structural media validation lives in ``test_release_assets.py`` and benchmark
evidence in ``test_release_evidence.py``; this file covers the gate wiring:
input-tree certification, fresh-checkout tracking, document links, prior-phase
evidence, gate assertions and the failure artifact.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.unit.release_fixtures import make_iso_bmff, make_png

REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFY_PHASE_PATH = REPO_ROOT / "scripts" / "verify_phase.py"

_REQUIRED_DOCS = (
    "README.md",
    "docs/architecture.md",
    "docs/data-flow.md",
    "docs/security-and-deployment.md",
    "README_ARGUS_CONTROL.md",
    "ARGUS_CONTROL_PRD.md",
    "ARGUS_CONTROL_MASTER_PROMPT.md",
    "AGENTS.md",
    "BUILD_STATUS.md",
)

_GIT_FALLBACK = Path(r"C:\Program Files\Git\cmd\git.exe")


@pytest.fixture(scope="module")
def verify_phase() -> ModuleType:
    spec = importlib.util.spec_from_file_location("argus_verify_phase_8", VERIFY_PHASE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sandbox(verify_phase: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the module's repository root at a temporary tree."""
    monkeypatch.setattr(verify_phase, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(verify_phase, "ARTIFACT_DIR", tmp_path / "artifacts" / "evaluation")
    return tmp_path


def _report(verify_phase: ModuleType) -> object:
    return verify_phase.GateReport(phase=8)


# ---------------------------------------------------------------------------
# A real throwaway git repository, so tracking checks exercise git itself.
# ---------------------------------------------------------------------------


def _git_executable() -> str | None:
    found = shutil.which("git")
    if found:
        return found
    return str(_GIT_FALLBACK) if _GIT_FALLBACK.is_file() else None


def _git(root: Path, *args: str) -> None:
    executable = _git_executable()
    assert executable is not None
    subprocess.run([executable, *args], cwd=str(root), check=True, capture_output=True)


def _minimal_release_tree() -> dict[str, str]:
    return {
        **{name: "x" * 400 for name in _REQUIRED_DOCS},
        "backend/app/main.py": "# runtime\n",
        "backend/app/config.py": "# runtime\n",
        "backend/tests/unit/test_x.py": "# test\n",
        "scripts/verify_phase.py": "# gate\n",
        "frontend/src/app/page.tsx": "// ui\n",
        "contracts/domain_enums.json": "{}\n",
        "backend/pyproject.toml": "[project]\n",
        "backend/requirements.lock.txt": "fastapi==0.141.1\n",
        "frontend/package.json": "{}\n",
        "frontend/package-lock.json": json.dumps({"lockfileVersion": 3}),
        "frontend/next.config.mjs": "export default {};\n",
        "frontend/tsconfig.json": "{}\n",
        "frontend/playwright.config.ts": "export default {};\n",
        "frontend/vitest.config.ts": "export default {};\n",
        ".env.example": "ARGUS_DB_PATH=\n",
        ".gitignore": "nothing-ignored-here\n",
        "datasets/dev/inputs/payments.csv": "payment_id\n",
        "datasets/holdout/inputs/payments.csv": "payment_id\n",
        "artifacts/benchmark/final.json": "{}\n",
        "artifacts/benchmark/final-rules-only.json": "{}\n",
        "artifacts/benchmark/final_summary.md": "summary\n",
    }


@pytest.fixture
def repo(sandbox: Path) -> Path:
    """A committed miniature repository shaped like the release-critical tree."""
    if _git_executable() is None:
        pytest.skip("git is not available")
    _git(sandbox, "init", "-q")
    _git(sandbox, "config", "user.email", "test@example.invalid")
    _git(sandbox, "config", "user.name", "ARGUS Test")
    for relative, content in _minimal_release_tree().items():
        path = sandbox / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(sandbox, "add", "-A")
    _git(sandbox, "commit", "-q", "-m", "release tree")
    return sandbox


def _certify(verify_phase: ModuleType, report: object) -> object:
    report.release_tree = verify_phase.capture_release_tree()
    return verify_phase.phase8_input_tree_certification(report)


# ---------------------------------------------------------------------------
# Input-tree certification.
# ---------------------------------------------------------------------------


def test_a_clean_committed_tree_is_certified(verify_phase: ModuleType, repo: Path) -> None:
    report = _report(verify_phase)
    step = _certify(verify_phase, report)
    assert step.status == "PASS", step.summary
    assert report.counts["release_input_tree_clean"] is True
    assert len(str(report.counts["release_commit"])) == 40


def test_the_phase8_artifact_is_the_only_permitted_dirty_path(
    verify_phase: ModuleType, repo: Path
) -> None:
    assert verify_phase.PHASE8_SELF_ARTIFACT == "artifacts/evaluation/phase-08.json"
    artifact = repo / "artifacts" / "evaluation" / "phase-08.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('{"status": "RUNNING"}', encoding="utf-8")
    step = _certify(verify_phase, _report(verify_phase))
    assert step.status == "PASS", step.summary

    # Any OTHER evaluation artifact is still surfaced, not hidden.
    (artifact.parent / "phase-07.json").write_text("{}", encoding="utf-8")
    step = _certify(verify_phase, _report(verify_phase))
    assert step.status == "FAIL"
    assert "phase-07.json" in step.summary


def test_an_untracked_runtime_module_fails_certification(
    verify_phase: ModuleType, repo: Path
) -> None:
    (repo / "backend" / "app" / "cors.py").write_text("# new runtime\n", encoding="utf-8")
    step = _certify(verify_phase, _report(verify_phase))
    assert step.status == "FAIL"
    assert "backend/app/cors.py" in step.summary


def test_a_modified_tracked_runtime_module_fails_certification(
    verify_phase: ModuleType, repo: Path
) -> None:
    (repo / "backend" / "app" / "main.py").write_text("# changed\n", encoding="utf-8")
    step = _certify(verify_phase, _report(verify_phase))
    assert step.status == "FAIL"
    assert "backend/app/main.py" in step.summary


def test_certification_summary_never_leaks_an_absolute_path(
    verify_phase: ModuleType, repo: Path
) -> None:
    (repo / "backend" / "app" / "cors.py").write_text("# new runtime\n", encoding="utf-8")
    step = _certify(verify_phase, _report(verify_phase))
    assert str(repo) not in step.summary
    assert ":\\" not in step.summary


def test_many_dirty_paths_are_bounded_in_the_summary(verify_phase: ModuleType, repo: Path) -> None:
    for index in range(40):
        (repo / "backend" / "app" / f"extra_{index}.py").write_text("x\n", encoding="utf-8")
    step = _certify(verify_phase, _report(verify_phase))
    assert step.status == "FAIL"
    assert "more)" in step.summary
    assert len(step.summary) < 2000


def test_certification_fails_when_the_tree_was_never_captured(
    verify_phase: ModuleType, sandbox: Path
) -> None:
    step = verify_phase.phase8_input_tree_certification(_report(verify_phase))
    assert step.status == "FAIL"
    assert "could not determine the certified commit" in step.summary


# ---------------------------------------------------------------------------
# Fresh-checkout readiness.
# ---------------------------------------------------------------------------


def test_a_fully_committed_tree_is_fresh_checkout_ready(
    verify_phase: ModuleType, repo: Path
) -> None:
    step = verify_phase.phase8_fresh_checkout_readiness(_report(verify_phase))
    assert step.status == "PASS", step.summary


def test_an_untracked_runtime_module_fails_fresh_checkout(
    verify_phase: ModuleType, repo: Path
) -> None:
    (repo / "backend" / "app" / "cors.py").write_text("# new runtime\n", encoding="utf-8")
    step = verify_phase.phase8_fresh_checkout_readiness(_report(verify_phase))
    assert step.status == "FAIL"
    assert "backend/app/cors.py" in step.summary


def test_an_unpinned_requirement_fails_fresh_checkout(verify_phase: ModuleType, repo: Path) -> None:
    (repo / "backend" / "requirements.lock.txt").write_text("fastapi\n", encoding="utf-8")
    step = verify_phase.phase8_fresh_checkout_readiness(_report(verify_phase))
    assert step.status == "FAIL"
    assert "unpinned" in step.summary


def _write_manifest(root: Path, manifest: dict[str, object]) -> None:
    path = root / "artifacts" / "release" / "submission-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _local_asset_manifest(root: Path) -> dict[str, object]:
    videos: dict[str, object] = {}
    for label, seed in (("primary", 1), ("backup", 2)):
        path = root / "artifacts" / "release" / "video" / f"{label}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = bytearray(make_iso_bmff(2 * 1024 * 1024))
        body[-1] = seed
        path.write_bytes(bytes(body))
        videos[label] = {
            "kind": "file",
            "path": f"artifacts/release/video/{label}.mp4",
            "sha256": hashlib.sha256(bytes(body)).hexdigest(),
        }
    shot = root / "artifacts" / "release" / "screenshots" / "dashboard.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(make_png(1280, 720))
    return {
        "manifest_version": "argus-release-manifest-v1",
        "videos": videos,
        "screenshots": [
            {
                "path": "artifacts/release/screenshots/dashboard.png",
                "traceable_artifact": "artifacts/benchmark/final.json",
                "traceable_values": ["{}"],
            }
        ],
    }


def test_an_untracked_manifest_fails_fresh_checkout(verify_phase: ModuleType, repo: Path) -> None:
    _write_manifest(repo, _local_asset_manifest(repo))
    step = verify_phase.phase8_fresh_checkout_readiness(_report(verify_phase))
    assert step.status == "FAIL"
    assert "submission-manifest.json" in step.summary


def test_an_untracked_screenshot_fails_fresh_checkout(verify_phase: ModuleType, repo: Path) -> None:
    _write_manifest(repo, _local_asset_manifest(repo))
    _git(repo, "add", "-A", "artifacts/release/submission-manifest.json")
    _git(repo, "add", "-A", "artifacts/release/video")
    _git(repo, "commit", "-q", "-m", "manifest and videos")
    step = verify_phase.phase8_fresh_checkout_readiness(_report(verify_phase))
    assert step.status == "FAIL"
    assert "screenshots/dashboard.png" in step.summary


def test_an_untracked_local_video_fails_fresh_checkout(
    verify_phase: ModuleType, repo: Path
) -> None:
    _write_manifest(repo, _local_asset_manifest(repo))
    _git(repo, "add", "-A", "artifacts/release/submission-manifest.json")
    _git(repo, "add", "-A", "artifacts/release/screenshots")
    _git(repo, "commit", "-q", "-m", "manifest and screenshot")
    step = verify_phase.phase8_fresh_checkout_readiness(_report(verify_phase))
    assert step.status == "FAIL"
    assert "video/primary.mp4" in step.summary


def test_a_fully_tracked_manifest_and_assets_pass(verify_phase: ModuleType, repo: Path) -> None:
    _write_manifest(repo, _local_asset_manifest(repo))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "release assets")
    step = verify_phase.phase8_fresh_checkout_readiness(_report(verify_phase))
    assert step.status == "PASS", step.summary


def test_a_remote_url_manifest_must_still_be_tracked(verify_phase: ModuleType, repo: Path) -> None:
    _write_manifest(
        repo,
        {
            "manifest_version": "argus-release-manifest-v1",
            "videos": {
                "primary": {"kind": "url", "url": "https://cdn.argus-demo.in/a.mp4"},
                "backup": {"kind": "url", "url": "https://cdn.argus-demo.in/b.mp4"},
            },
            "screenshots": [],
        },
    )
    step = verify_phase.phase8_fresh_checkout_readiness(_report(verify_phase))
    assert step.status == "FAIL"
    assert "submission-manifest.json" in step.summary


# ---------------------------------------------------------------------------
# Submission assets at the gate level.
# ---------------------------------------------------------------------------


def test_missing_manifest_fails_and_names_the_owner_action(
    verify_phase: ModuleType, sandbox: Path
) -> None:
    step = verify_phase.phase8_submission_assets(_report(verify_phase))
    assert step.status == "FAIL"
    assert "submission-manifest.json" in step.summary
    assert "owner action" in step.summary


def test_an_unparseable_manifest_fails(verify_phase: ModuleType, sandbox: Path) -> None:
    path = sandbox / "artifacts" / "release" / "submission-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    step = verify_phase.phase8_submission_assets(_report(verify_phase))
    assert step.status == "FAIL"
    assert "not parseable" in step.summary


def test_a_structurally_real_manifest_passes_the_gate_step(
    verify_phase: ModuleType, sandbox: Path
) -> None:
    (sandbox / "artifacts" / "benchmark").mkdir(parents=True, exist_ok=True)
    (sandbox / "artifacts" / "benchmark" / "final.json").write_text("{}", encoding="utf-8")
    _write_manifest(sandbox, _local_asset_manifest(sandbox))
    report = _report(verify_phase)
    step = verify_phase.phase8_submission_assets(report)
    assert step.status == "PASS", step.summary
    assert report.counts["release_local_videos"] == 2
    assert "not proven reachable offline" in step.summary


# ---------------------------------------------------------------------------
# Release documents.
# ---------------------------------------------------------------------------


def _write_docs(root: Path) -> None:
    for relative in _REQUIRED_DOCS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x" * 400, encoding="utf-8")


def test_valid_documents_and_links_pass(verify_phase: ModuleType, sandbox: Path) -> None:
    _write_docs(sandbox)
    (sandbox / "README.md").write_text(
        "x" * 400 + "\n[arch](docs/architecture.md)\n", encoding="utf-8"
    )
    step = verify_phase.phase8_release_documents(_report(verify_phase))
    assert step.status == "PASS", step.summary


def test_broken_internal_link_fails_the_document_check(
    verify_phase: ModuleType, sandbox: Path
) -> None:
    _write_docs(sandbox)
    (sandbox / "README.md").write_text(
        "x" * 400 + "\n[gone](docs/does-not-exist.md)\n", encoding="utf-8"
    )
    step = verify_phase.phase8_release_documents(_report(verify_phase))
    assert step.status == "FAIL"
    assert "broken link" in step.summary


def test_a_link_escaping_the_repository_fails_even_if_the_target_exists(
    verify_phase: ModuleType, sandbox: Path
) -> None:
    # A real file outside the sandbox repository root.
    outside = sandbox.parent / "outside-secrets.md"
    outside.write_text("# outside the repository\n", encoding="utf-8")
    _write_docs(sandbox)
    (sandbox / "docs" / "architecture.md").write_text(
        "x" * 400 + "\n[escape](../../outside-secrets.md)\n", encoding="utf-8"
    )
    step = verify_phase.phase8_release_documents(_report(verify_phase))
    assert step.status == "FAIL"
    assert "traverse upwards" in step.summary or "escapes the repository" in step.summary


def test_missing_release_document_fails(verify_phase: ModuleType, sandbox: Path) -> None:
    step = verify_phase.phase8_release_documents(_report(verify_phase))
    assert step.status == "FAIL"
    assert "missing required document" in step.summary


# ---------------------------------------------------------------------------
# Prior-phase evidence.
# ---------------------------------------------------------------------------


def _write_phase_artifacts(root: Path, **overrides: str) -> None:
    directory = root / "artifacts" / "evaluation"
    directory.mkdir(parents=True, exist_ok=True)
    for phase in range(8):
        commands = (
            [{"name": "unit-tests-audit_service", "status": "PASS", "summary": "audit ok"}]
            if phase == 5
            else []
        )
        payload = {"status": overrides.get(f"phase{phase}", "PASS"), "commands": commands}
        (directory / f"phase-{phase:02d}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_complete_passing_prior_evidence_is_accepted(
    verify_phase: ModuleType, sandbox: Path
) -> None:
    _write_phase_artifacts(sandbox)
    step = verify_phase.phase8_prior_phase_evidence(_report(verify_phase))
    assert step.status == "PASS", step.summary


def test_prior_phase_evidence_requires_pass_artifacts(
    verify_phase: ModuleType, sandbox: Path
) -> None:
    _write_phase_artifacts(sandbox, phase6="FAIL")
    step = verify_phase.phase8_prior_phase_evidence(_report(verify_phase))
    assert step.status == "FAIL"
    assert "phase-06.json" in step.summary


def test_missing_audit_completeness_evidence_fails(verify_phase: ModuleType, sandbox: Path) -> None:
    _write_phase_artifacts(sandbox)
    (sandbox / "artifacts" / "evaluation" / "phase-05.json").write_text(
        json.dumps({"status": "PASS", "commands": []}), encoding="utf-8"
    )
    step = verify_phase.phase8_prior_phase_evidence(_report(verify_phase))
    assert step.status == "FAIL"
    assert "audit-completeness" in step.summary


# ---------------------------------------------------------------------------
# Gate assertions and the failure artifact.
# ---------------------------------------------------------------------------


def test_gate_assertions_fail_when_a_mandatory_step_never_ran(
    verify_phase: ModuleType,
) -> None:
    step = verify_phase.phase8_gate_assertions(verify_phase.GateReport(phase=8))
    assert step.status == "FAIL"
    assert "mandatory step never ran" in step.summary


def test_gate_assertions_require_every_mandatory_step_to_pass(
    verify_phase: ModuleType,
) -> None:
    report = verify_phase.GateReport(phase=8)
    for name in verify_phase.PHASE8_MANDATORY_STEPS:
        status = "FAIL" if name == "release-submission-assets" else "PASS"
        report.steps.append(verify_phase.StepResult(name, name, status, 0.0, "detail"))
    step = verify_phase.phase8_gate_assertions(report)
    assert step.status == "FAIL"
    assert "release-submission-assets" in step.summary

    for entry in report.steps:
        if entry.name == "release-submission-assets":
            entry.status = "PASS"
    assert verify_phase.phase8_gate_assertions(report).status == "PASS"


def test_input_tree_certification_is_mandatory(verify_phase: ModuleType) -> None:
    assert "release-input-tree-certification" in verify_phase.PHASE8_MANDATORY_STEPS


def test_the_artifact_never_records_this_machines_absolute_paths(
    verify_phase: ModuleType, tmp_path: Path
) -> None:
    """Committed evaluation artifacts are public: the checkout location is not evidence."""
    root = str(verify_phase.REPO_ROOT)
    report = verify_phase.GateReport(phase=8)
    report.started_at_utc = "2026-09-05T00:00:00+00:00"
    report.steps.append(
        verify_phase.StepResult(
            "backend-pytest-full",
            rf"{root}\.venv\Scripts\python.exe -m pytest backend/tests",
            "PASS",
            1.0,
            f"basetemp {root}/tmp/x",
        )
    )
    verify_phase.finalize_run(report, artifact_dir=tmp_path)
    blob = (tmp_path / "phase-08.json").read_text(encoding="utf-8")
    assert root not in blob
    assert root.replace(chr(92), "/") not in blob
    assert "<repo>" in blob
    # The repository-relative remainder is preserved, not blanked.
    assert "-m pytest backend/tests" in blob


def test_phase8_writes_a_truthful_fail_artifact(verify_phase: ModuleType, tmp_path: Path) -> None:
    report = verify_phase.GateReport(phase=8)
    report.started_at_utc = "2026-09-05T00:00:00+00:00"
    report.steps.append(
        verify_phase.StepResult("release-submission-assets", "assets", "FAIL", 0.1, "videos absent")
    )
    verify_phase.finalize_run(report, artifact_dir=tmp_path)
    artifact = json.loads((tmp_path / "phase-08.json").read_text(encoding="utf-8"))
    assert artifact["phase"] == 8
    assert artifact["phase_name"] == "Submission Release"
    assert artifact["status"] == "FAIL"
    assert artifact["commands"][0]["status"] == "FAIL"
