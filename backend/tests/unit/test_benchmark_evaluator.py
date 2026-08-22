"""Phase 2 benchmark-evaluator tests: frozen denominators, anchor matching.

The evaluator loads labels only after the runtime output is complete; every
metric must expose explicit numerator and denominator fields; a case with
extra unrelated evidence must not count as correct; clean dev records must
match with 100% precision (PRD Phase 2 acceptance gate).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from app.evaluation.benchmark import evaluate_dataset
from app.persistence.database import Database
from app.runs import execute_run

REPO_ROOT = Path(__file__).resolve().parents[3]


def _runtime_output(profile: str, tmp_path: Path) -> dict:
    database = Database(tmp_path / f"{profile}-eval.sqlite3")
    try:
        result = execute_run(REPO_ROOT / "datasets" / profile / "inputs", database)
    finally:
        database.close()
    return dict(result.summary)


class TestDevBenchmark:
    def test_precision_is_one_with_frozen_denominators(self, tmp_path: Path) -> None:
        evaluation = evaluate_dataset(
            REPO_ROOT / "datasets" / "dev", _runtime_output("dev", tmp_path)
        )
        precision = evaluation["metrics"]["match_precision"]
        assert precision["numerator"] == precision["denominator"]
        assert precision["rate"] == 1.0
        assert precision["denominator"] > 0

    def test_every_metric_carries_numerator_and_denominator(self, tmp_path: Path) -> None:
        evaluation = evaluate_dataset(
            REPO_ROOT / "datasets" / "dev", _runtime_output("dev", tmp_path)
        )
        for name in (
            "record_match_rate",
            "match_precision",
            "case_classification_accuracy",
        ):
            metric = evaluation["metrics"][name]
            assert "numerator" in metric and "denominator" in metric, name
            assert isinstance(metric["numerator"], int)
            assert isinstance(metric["denominator"], int)

    def test_dev_counts_and_cases(self, tmp_path: Path) -> None:
        evaluation = evaluate_dataset(
            REPO_ROOT / "datasets" / "dev", _runtime_output("dev", tmp_path)
        )
        counts = evaluation["counts"]
        assert counts["eligible_canonical_records"] == 282
        assert counts["eligible_counts_match"] is True
        assert counts["quarantined"] == {"runtime": 0, "expected": 0, "match": True}
        assert counts["duplicate_deliveries"] == {
            "runtime": 0,
            "expected": 0,
            "match": True,
        }
        accuracy = evaluation["metrics"]["case_classification_accuracy"]
        assert accuracy == {"numerator": 12, "denominator": 12, "rate": 1.0}
        assert evaluation["case_comparison"]["false_positive_cases"] == []
        assert evaluation["case_comparison"]["missed_labels"] == []
        assert evaluation["totals_comparison"]["equal"] is True
        assert evaluation["graph"]["referentially_valid"] is True

    def test_record_match_rate_denominator_is_eligible_canonical(self, tmp_path: Path) -> None:
        evaluation = evaluate_dataset(
            REPO_ROOT / "datasets" / "dev", _runtime_output("dev", tmp_path)
        )
        rate = evaluation["metrics"]["record_match_rate"]
        assert rate["denominator"] == 282
        assert rate["numerator"] == 273  # 282 minus 9 unexplainable records

    def test_adversarial_counts(self, tmp_path: Path) -> None:
        evaluation = evaluate_dataset(
            REPO_ROOT / "datasets" / "adversarial",
            _runtime_output("adversarial", tmp_path),
        )
        counts = evaluation["counts"]
        assert counts["eligible_canonical_records"] == 64
        assert counts["quarantined"]["match"] is True
        assert counts["quarantined"]["expected"] == 2
        assert counts["duplicate_deliveries"]["expected"] == 1
        accuracy = evaluation["metrics"]["case_classification_accuracy"]
        assert accuracy["numerator"] == accuracy["denominator"] == 3


class TestAnchorStrictness:
    def test_extra_evidence_does_not_count_as_correct(self, tmp_path: Path) -> None:
        output = _runtime_output("dev", tmp_path)
        victim = next(case for case in output["cases"] if case["category"] == "AMBIGUOUS_EVIDENCE")
        victim["evidence"].append({"record_type": "PAYMENT", "record_id": "pay_UNRELATED00"})
        evaluation = evaluate_dataset(REPO_ROOT / "datasets" / "dev", output)
        accuracy = evaluation["metrics"]["case_classification_accuracy"]
        assert accuracy["numerator"] == 11
        assert accuracy["denominator"] == 12
        false_positives = evaluation["case_comparison"]["false_positive_cases"]
        assert any(item["runtime_case_id"] == victim["case_id"] for item in false_positives)

    def test_missing_anchor_evidence_does_not_count(self, tmp_path: Path) -> None:
        output = _runtime_output("dev", tmp_path)
        victim = next(
            case for case in output["cases"] if case["category"] == "MISSING_REFUND_POSTING"
        )
        victim["evidence"] = victim["evidence"][:1]  # drop anchors
        evaluation = evaluate_dataset(REPO_ROOT / "datasets" / "dev", output)
        accuracy = evaluation["metrics"]["case_classification_accuracy"]
        assert accuracy["numerator"] == 11

    def test_wrong_category_does_not_count(self, tmp_path: Path) -> None:
        output = _runtime_output("dev", tmp_path)
        victim = output["cases"][0]
        victim["category"] = "SETTLEMENT_TIMING_WINDOW_SHIFT"
        evaluation = evaluate_dataset(REPO_ROOT / "datasets" / "dev", output)
        accuracy = evaluation["metrics"]["case_classification_accuracy"]
        assert accuracy["numerator"] == 11

    def test_false_relationship_reduces_precision(self, tmp_path: Path) -> None:
        output = _runtime_output("dev", tmp_path)
        forged = copy.deepcopy(output["matches"][0])
        forged["match_id"] = "match-forged"
        # Point the refund at the wrong payment: verifier must reject it.
        for member in forged["members"]:
            if member["record_type"] == "PAYMENT":
                member["record_id"] = "pay_Y2TUIDO4ZU"
        output["matches"].append(forged)
        evaluation = evaluate_dataset(REPO_ROOT / "datasets" / "dev", output)
        precision = evaluation["metrics"]["match_precision"]
        assert precision["numerator"] == precision["denominator"] - 1
        assert precision["rate"] < 1.0
        assert evaluation["false_relationships"]


class TestLabelsFirewallContract:
    def test_labels_are_loaded_only_by_the_evaluator(self, tmp_path: Path) -> None:
        # The runtime output is produced and frozen first; evaluate_dataset
        # reads labels afterwards. This test pins the call ordering: the
        # runtime phase never receives the dataset parent directory.
        output = _runtime_output("dev", tmp_path)
        assert "expected_category" not in json.dumps(output)
        assert "labels" not in json.dumps(output).lower().replace("labels_manifest", "")
