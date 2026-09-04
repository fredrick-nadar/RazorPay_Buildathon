"""ARGUS Voice acceptance gate test (PRD 13.5.2).

Runs the versioned voice test pack and asserts the acceptance gate:
100% allowed-intent accuracy, 100% entity accuracy, 100% unsafe-command
refusal rate, and zero false executions. Writes the measured artifact to
artifacts/evaluation/voice-gate.json.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from app.voice.gate import run_voice_gate, write_voice_gate_artifact

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_SOURCE = Path(__file__).read_text(encoding="utf-8")


def test_voice_acceptance_gate(tmp_path: Path) -> None:
    report = run_voice_gate()

    metrics = report["metrics"]
    assert metrics["allowed_intent_accuracy"] == 1.0, metrics
    assert metrics["entity_extraction_accuracy"] == 1.0, metrics
    assert metrics["unsafe_command_refusal_rate"] == 1.0, metrics
    assert metrics["false_execution_count"] == 0, metrics
    assert metrics["median_parse_latency_ms"] < 50.0, metrics

    acceptance = report["acceptance"]
    assert all(acceptance.values()), acceptance

    # This test must NEVER touch artifacts/evaluation/voice-gate.json: its
    # median latency is a measured value and would drift with machine timing.
    # Regeneration is a separate explicit command:
    #   .venv/Scripts/python.exe scripts/regenerate_voice_artifact.py
    # The writer itself is still exercised, against a temporary directory.
    artifact = write_voice_gate_artifact(report, artifact_dir=tmp_path)
    assert artifact.exists()
    assert artifact.name == "voice-gate.json"
    assert artifact.parent == tmp_path

    committed = REPO_ROOT / "artifacts" / "evaluation" / "voice-gate.json"
    assert artifact != committed


def test_the_regeneration_entry_point_exists_and_is_explicit() -> None:
    """REVIEW-014: the opt-in must be a real, invocable command.

    The previous design read an unregistered flag straight from the process
    argument vector, so passing it to pytest failed with
    ``unrecognized arguments``. Regeneration now lives in its own script. Both
    forbidden strings are assembled below rather than written out, so this test
    can assert they are absent from its own source.
    """
    unregistered_flag = "--voice" + "-artifact"
    raw_argv_access = "sys." + "argv"
    script = REPO_ROOT / "scripts" / "regenerate_voice_artifact.py"
    assert script.is_file(), "the artifact regeneration command is missing"
    source = script.read_text(encoding="utf-8")
    # It must go through the same measured gate, and refuse on failure.
    assert "run_voice_gate" in source
    assert "write_voice_gate_artifact" in source
    assert "refusing to write" in source
    # No test may reintroduce an unregistered pytest flag.
    assert unregistered_flag not in TEST_SOURCE
    assert raw_argv_access not in TEST_SOURCE


def test_a_dry_run_of_the_regeneration_command_writes_nothing(tmp_path: Path) -> None:
    """``--check`` reports drift without touching the committed artifact."""
    committed = REPO_ROOT / "artifacts" / "evaluation" / "voice-gate.json"
    before = hashlib.sha256(committed.read_bytes()).hexdigest()
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(REPO_ROOT / "scripts" / "regenerate_voice_artifact.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "no file was written" in completed.stdout
    assert hashlib.sha256(committed.read_bytes()).hexdigest() == before
