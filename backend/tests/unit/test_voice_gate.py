"""ARGUS Voice acceptance gate test (PRD 13.5.2).

Runs the versioned voice test pack and asserts the acceptance gate:
100% allowed-intent accuracy, 100% entity accuracy, 100% unsafe-command
refusal rate, and zero false executions. Writes the measured artifact to
artifacts/evaluation/voice-gate.json.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.voice.gate import run_voice_gate, write_voice_gate_artifact

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_voice_acceptance_gate() -> None:
    report = run_voice_gate()

    metrics = report["metrics"]
    assert metrics["allowed_intent_accuracy"] == 1.0, metrics
    assert metrics["entity_extraction_accuracy"] == 1.0, metrics
    assert metrics["unsafe_command_refusal_rate"] == 1.0, metrics
    assert metrics["false_execution_count"] == 0, metrics
    assert metrics["median_parse_latency_ms"] < 50.0, metrics

    acceptance = report["acceptance"]
    assert all(acceptance.values()), acceptance

    if "--voice-artifact" in sys.argv:  # explicit artifact regeneration
        write_voice_gate_artifact(report)

    artifact = write_voice_gate_artifact(report)
    assert artifact.exists()
    assert artifact.name == "voice-gate.json"
