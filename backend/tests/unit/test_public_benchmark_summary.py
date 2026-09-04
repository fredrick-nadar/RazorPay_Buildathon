from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.evaluation.public_summary import (
    build_public_benchmark_summary,
    canonical_benchmark_digest,
)


def _final_report() -> dict[str, object]:
    path = Path(__file__).resolve().parents[3] / "artifacts" / "benchmark" / "final.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_public_summary_contains_only_measured_aggregate_values() -> None:
    report = _final_report()
    summary = build_public_benchmark_summary(report, source_sha256="a" * 64)

    assert (
        summary["eligible_records"] == report["evaluation"]["counts"]["eligible_canonical_records"]
    )
    measured_precision = report["evaluation"]["metrics"]["match_precision"]
    assert summary["match_precision"] == {
        "numerator": measured_precision["numerator"],
        "denominator": measured_precision["denominator"],
        "rate": measured_precision["rate"],
    }
    assert (
        summary["false_verifier_pass_count"]
        == report["evaluation"]["verification"]["false_verifier_pass_count"]
    )
    assert "case_comparison" not in summary
    assert "matched_pairs" not in json.dumps(summary)


def test_public_summary_refuses_a_failed_benchmark() -> None:
    report = copy.deepcopy(_final_report())
    report["problems"] = ["measured failure"]

    with pytest.raises(ValueError, match="passing benchmark"):
        build_public_benchmark_summary(report, source_sha256="b" * 64)


def test_committed_public_summary_matches_final_artifact() -> None:
    artifact_dir = Path(__file__).resolve().parents[3] / "artifacts" / "benchmark"
    report = json.loads((artifact_dir / "final.json").read_text(encoding="utf-8"))
    published = json.loads((artifact_dir / "public-summary.json").read_text(encoding="utf-8"))

    assert published == build_public_benchmark_summary(
        report,
        source_sha256=canonical_benchmark_digest(report),
    )
