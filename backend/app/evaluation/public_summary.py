"""Publish a label-free subset of a completed benchmark artifact for the UI."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _required_mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"benchmark field {path!r} must be an object")
    return value


def _required_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"benchmark field {path!r} must be an integer")
    return value


def _rate(metric: dict[str, Any], path: str) -> dict[str, int | float]:
    numerator = _required_int(metric.get("numerator"), f"{path}.numerator")
    denominator = _required_int(metric.get("denominator"), f"{path}.denominator")
    rate = metric.get("rate")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise ValueError(f"benchmark field {path + '.rate'!r} must be numeric")
    if denominator <= 0 or numerator < 0 or numerator > denominator:
        raise ValueError(f"benchmark field {path!r} has an invalid denominator")
    return {"numerator": numerator, "denominator": denominator, "rate": float(rate)}


def canonical_benchmark_digest(report: dict[str, Any]) -> str:
    """Hash semantic JSON content consistently across checkout line endings."""
    material = json.dumps(
        report,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def build_public_benchmark_summary(report: dict[str, Any], *, source_sha256: str) -> dict[str, Any]:
    """Extract measured aggregate values without serializing evaluator labels."""
    evaluation = _required_mapping(report.get("evaluation"), "evaluation")
    counts = _required_mapping(evaluation.get("counts"), "evaluation.counts")
    metrics = _required_mapping(evaluation.get("metrics"), "evaluation.metrics")
    verification = _required_mapping(evaluation.get("verification"), "evaluation.verification")
    replay = _required_mapping(report.get("replay_diagnostics"), "replay_diagnostics")

    if report.get("problems") != []:
        raise ValueError("only a passing benchmark artifact can be published")
    if len(source_sha256) != 64:
        raise ValueError("source_sha256 must be a full SHA-256 hex digest")

    return {
        "schema_version": "argus-public-benchmark-v1",
        "source_artifact": "artifacts/benchmark/final.json",
        "source_sha256": source_sha256,
        "source_digest_basis": "CANONICAL_JSON_V1",
        "benchmark_version": str(report.get("benchmark_version", "")),
        "dataset": str(report.get("dataset", "")),
        "mode": str(report.get("mode", "")),
        "provider": str(report.get("provider", "")),
        "eligible_records": _required_int(
            counts.get("eligible_canonical_records"),
            "evaluation.counts.eligible_canonical_records",
        ),
        "match_precision": _rate(
            _required_mapping(metrics.get("match_precision"), "match_precision"),
            "evaluation.metrics.match_precision",
        ),
        "record_match_rate": _rate(
            _required_mapping(metrics.get("record_match_rate"), "record_match_rate"),
            "evaluation.metrics.record_match_rate",
        ),
        "case_classification": _rate(
            _required_mapping(
                metrics.get("case_classification_accuracy"),
                "case_classification_accuracy",
            ),
            "evaluation.metrics.case_classification_accuracy",
        ),
        "false_verifier_pass_count": _required_int(
            verification.get("false_verifier_pass_count"),
            "evaluation.verification.false_verifier_pass_count",
        ),
        "duplicate_correction_count": _required_int(
            replay.get("duplicate_corrections_detected"),
            "replay_diagnostics.duplicate_corrections_detected",
        ),
    }
