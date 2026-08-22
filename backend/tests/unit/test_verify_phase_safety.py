"""Regression tests for verifier portability: safe console output and fail-safe artifacts.

These tests import scripts/verify_phase.py as a standalone module and exercise
its safety helpers directly. They never run the actual gate steps.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFY_PHASE_PATH = REPO_ROOT / "scripts" / "verify_phase.py"


@pytest.fixture(scope="module")
def verify_phase() -> ModuleType:
    spec = importlib.util.spec_from_file_location("argus_verify_phase", VERIFY_PHASE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestSafeUnicodeOutput:
    def test_unencodable_chars_are_backslash_replaced(self, verify_phase: ModuleType) -> None:
        # Next.js build output contains "\u25cb" (○), which CP1252 cannot encode.
        original = "\u25cf \u25cb static content \u2713"
        safe = verify_phase.safe_text(original, "cp1252")
        safe.encode("cp1252")  # must not raise
        assert "static content" in safe
        assert safe != original

    def test_ascii_passes_through_unchanged(self, verify_phase: ModuleType) -> None:
        assert verify_phase.safe_text("54 passed in 0.5s", "cp1252") == "54 passed in 0.5s"

    def test_rupee_sign_replaced_under_cp1252(self, verify_phase: ModuleType) -> None:
        text = "\u20b9 78,640.00 variance"
        utf8_safe = verify_phase.safe_text(text, "utf-8")
        assert utf8_safe == text
        cp1252_safe = verify_phase.safe_text(text, "cp1252")
        cp1252_safe.encode("cp1252")  # must not raise
        assert "78,640.00 variance" in cp1252_safe


class TestFailSafeArtifacts:
    def test_stale_pass_artifact_is_replaced_by_running_at_run_start(
        self, verify_phase: ModuleType, tmp_path: Path
    ) -> None:
        artifact_dir = tmp_path / "evaluation"
        artifact_dir.mkdir()
        stale = artifact_dir / "phase-00.json"
        stale.write_text(json.dumps({"status": "PASS", "fake": "stale"}), encoding="utf-8")

        report = verify_phase.GateReport(phase=0)
        report.started_at_utc = "2026-08-22T00:00:00+00:00"
        verify_phase.begin_run(report, artifact_dir)

        current = json.loads(stale.read_text(encoding="utf-8"))
        assert current["status"] == "RUNNING"
        assert "fake" not in current

    def test_unexpected_exception_writes_fail_artifact_with_type_and_step(
        self, verify_phase: ModuleType, tmp_path: Path
    ) -> None:
        report = verify_phase.GateReport(phase=0)
        report.started_at_utc = "2026-08-22T00:00:00+00:00"
        report.steps.append(
            verify_phase.StepResult("frontend-build", "npm run build", "PASS", 1.0, "ok")
        )
        verify_phase.record_unexpected_failure(
            report, "backend-boot-health", RuntimeError("simulated crash")
        )
        verify_phase.finalize_run(
            report, artifact_dir=tmp_path, forced_failure="backend-boot-health"
        )

        artifact = json.loads((tmp_path / "phase-00.json").read_text(encoding="utf-8"))
        assert artifact["status"] == "FAIL"
        assert artifact["failed_step"] == "backend-boot-health"
        assert "RuntimeError" in artifact["known_failures"][0]
        assert "backend-boot-health" in artifact["known_failures"][0]

    def test_successful_finalize_reports_pass_without_failed_step(
        self, verify_phase: ModuleType, tmp_path: Path
    ) -> None:
        report = verify_phase.GateReport(phase=0)
        report.started_at_utc = "2026-08-22T00:00:00+00:00"
        report.steps.append(
            verify_phase.StepResult("backend-pytest", "pytest", "PASS", 1.0, "1 passed")
        )
        verify_phase.finalize_run(report, artifact_dir=tmp_path)

        artifact = json.loads((tmp_path / "phase-00.json").read_text(encoding="utf-8"))
        assert artifact["status"] == "PASS"
        assert artifact["failed_step"] is None


class TestMissingBinaryIsStepFailureNotCrash:
    def test_run_command_returns_fail_when_binary_is_missing(
        self, verify_phase: ModuleType, tmp_path: Path
    ) -> None:
        missing = tmp_path / "definitely-not-a-real-binary.exe"
        step = verify_phase.run_command("probe-missing", [str(missing)], tmp_path, timeout_s=10)
        assert step.status == "FAIL"
        assert "could not execute" in step.summary


class TestUniquePytestBasetemp:
    def test_consecutive_invocations_get_different_basetemps(
        self, verify_phase: ModuleType
    ) -> None:
        first = verify_phase.new_basetemp(0)
        second = verify_phase.new_basetemp(0)
        try:
            assert first != second, "basetemp directories must be unique per invocation"
            assert first.parent == verify_phase.TMP_DIR
            assert second.parent == verify_phase.TMP_DIR
            assert first.name.startswith("pytest-phase-00-")
            assert second.name.startswith("pytest-phase-00-")
        finally:
            # Best-effort cleanup of exactly the directories this test created.
            shutil.rmtree(first, ignore_errors=True)
            shutil.rmtree(second, ignore_errors=True)

    def test_basetemp_args_point_at_the_unique_directory(self, verify_phase: ModuleType) -> None:
        basetemp = verify_phase.new_basetemp(0)
        try:
            args = verify_phase.pytest_args(basetemp)
            assert "--basetemp" in args
            assert args[args.index("--basetemp") + 1] == str(basetemp)
            assert "-p" in args and "no:cacheprovider" in args
        finally:
            shutil.rmtree(basetemp, ignore_errors=True)


class TestCappedOptionalStepTimeout:
    def test_timeout_kills_tree_records_nonblocking_fail_and_finalizes(
        self, verify_phase: ModuleType, tmp_path: Path
    ) -> None:
        sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
        step = verify_phase.run_capped_step(
            "sleeper-step", sleeper, tmp_path, timeout_s=2, gate_blocking=False
        )
        assert step.status == "FAIL"
        assert "timed out after 2s" in step.summary
        assert step.duration_s < 30, "the cap must actually bound the step duration"

        report = verify_phase.GateReport(phase=0)
        report.started_at_utc = "2026-08-22T00:00:00+00:00"
        report.steps.append(step)
        verify_phase.finalize_run(report, artifact_dir=tmp_path)

        artifact = json.loads((tmp_path / "phase-00.json").read_text(encoding="utf-8"))
        assert artifact["status"] == "PASS", "non-blocking timeout must not fail the gate"
        assert "non-blocking failure: sleeper-step" in artifact["known_failures"]
        assert artifact["failed_step"] is None
