"""Regenerate the measured voice acceptance-gate artifact, deliberately.

Ordinary `pytest` must never rewrite `artifacts/evaluation/voice-gate.json`:
`median_parse_latency_ms` is a MEASURED value, and letting a test overwrite it
made the committed number drift with local machine timing.

Regeneration is therefore a separate, explicit command:

    .venv\\Scripts\\python.exe scripts/regenerate_voice_artifact.py

It runs the same versioned voice pack the gate test runs, refuses to write when
the acceptance gate fails, and prints the before/after values so a reviewer can
see exactly what changed. Use --check to report drift without writing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.voice.gate import run_voice_gate, write_voice_gate_artifact  # noqa: E402

ARTIFACT = REPO_ROOT / "artifacts" / "evaluation" / "voice-gate.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "(absent)"


def _latency(payload: dict[str, Any]) -> Any:
    metrics = payload.get("metrics")
    return metrics.get("median_parse_latency_ms") if isinstance(metrics, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether a fresh run would change the artifact; write nothing",
    )
    args = parser.parse_args()

    report = run_voice_gate()
    acceptance = report["acceptance"]
    if not (isinstance(acceptance, dict) and all(acceptance.values())):
        print("voice acceptance gate FAILED; refusing to write the artifact", file=sys.stderr)
        print(json.dumps(acceptance, indent=2, sort_keys=True), file=sys.stderr)
        return 1

    before_digest = _digest(ARTIFACT)
    before_latency = (
        _latency(json.loads(ARTIFACT.read_text(encoding="utf-8"))) if ARTIFACT.is_file() else None
    )

    if args.check:
        print(f"committed sha256          : {before_digest}")
        print(f"committed median latency  : {before_latency} ms")
        print(f"freshly measured latency  : {_latency(report)} ms")
        print("no file was written (--check)")
        return 0

    path = write_voice_gate_artifact(report)
    print(f"wrote {path.relative_to(REPO_ROOT).as_posix()}")
    print(f"  median latency: {before_latency} ms -> {_latency(report)} ms")
    print(f"  sha256        : {before_digest} -> {_digest(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
