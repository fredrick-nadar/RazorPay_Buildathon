"""Phase 2 case tests: four categories, anchors, variance vs affected.

Runs over the committed dev and adversarial ``inputs`` (runtime-readable)
and, where assertions need ground truth, over ``labels`` from the evaluator
side - tests are evaluator-side code and may read labels; runtime code
cannot (see test_label_isolation).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.enums import ExceptionCategory
from app.importers.ingest import ingest_inputs
from app.reconciliation.detectors import reconcile

REPO_ROOT = Path(__file__).resolve().parents[3]


def dev_inputs() -> Path:
    return REPO_ROOT / "datasets" / "dev" / "inputs"


def adversarial_inputs() -> Path:
    return REPO_ROOT / "datasets" / "adversarial" / "inputs"


def load_labels(profile: str) -> dict:
    return json.loads(
        (REPO_ROOT / "datasets" / profile / "labels" / "labels.json").read_text(encoding="utf-8")
    )


class TestDevCases:
    def test_twelve_cases_three_per_category(self) -> None:
        result = reconcile(ingest_inputs(dev_inputs()).records)
        by_category: dict[str, int] = {}
        for case in result.cases:
            by_category[case.category.value] = by_category.get(case.category.value, 0) + 1
        assert by_category == {
            "DUPLICATE_LEDGER_POSTING": 3,
            "MISSING_REFUND_POSTING": 3,
            "SETTLEMENT_TIMING_WINDOW_SHIFT": 3,
            "AMBIGUOUS_EVIDENCE": 3,
        }

    def test_runtime_cases_match_label_anchors_exactly(self) -> None:
        result = reconcile(ingest_inputs(dev_inputs()).records)
        labels = load_labels("dev")
        runtime_evidence = [
            (
                case.category.value,
                sorted(f"{item.record_type}:{item.record_id}" for item in case.evidence),
            )
            for case in result.cases
        ]
        for label in labels["cases"]:
            anchors = sorted(f"{_type_of(item)}:{item}" for item in label["expected_evidence_ids"])
            assert (label["expected_category"], anchors) in runtime_evidence, label["case_id"]
        assert len(result.cases) == len(labels["cases"])

    def test_variance_affected_and_null_delta_semantics(self) -> None:
        result = reconcile(ingest_inputs(dev_inputs()).records)
        labels = load_labels("dev")
        payments = {
            record.payment_id: record for record in ingest_inputs(dev_inputs()).records.payments
        }
        label_by_payment = {}
        label_by_refund = {}
        for label in labels["cases"]:
            for item in label["expected_evidence_ids"]:
                if item.startswith("pay_"):
                    label_by_payment[item] = label
                if item.startswith("rfd_"):
                    label_by_refund[item] = label
        for case in result.cases:
            assert case.proposed_delta_paise is None
            if case.category == ExceptionCategory.DUPLICATE_LEDGER_POSTING:
                payment_id = next(
                    item.record_id for item in case.evidence if item.record_type == "PAYMENT"
                )
                net = int(payments[payment_id].net_paise)
                # variance = observed minus expected = the extra posting
                assert case.variance_paise == net
                assert case.affected_amount_paise == net
                assert case.variance_scope == "LEDGER"
            elif case.category == ExceptionCategory.MISSING_REFUND_POSTING:
                assert case.variance_paise > 0
                assert case.affected_amount_paise == case.variance_paise
                assert case.variance_scope == "LEDGER"
            elif case.category == ExceptionCategory.SETTLEMENT_TIMING_WINDOW_SHIFT:
                assert case.variance_paise == 0
                assert case.affected_amount_paise > 0
            else:  # twins: zero aggregate variance, non-zero affected amount
                assert case.variance_paise == 0
                assert case.affected_amount_paise > 0

    def test_every_accepted_row_matched_or_cased(self) -> None:
        result = reconcile(ingest_inputs(dev_inputs()).records)
        assert result.unaccounted_record_keys == frozenset()
        matched_or_cased = result.matched_record_keys | result.case_evidence_keys
        assert len(matched_or_cased) == 282


def _type_of(record_id: str) -> str:
    for prefix, record_type in (
        ("pay_", "PAYMENT"),
        ("rfd_", "REFUND"),
        ("stl_", "SETTLEMENT"),
        ("bnk_", "BANK_ENTRY"),
        ("led_", "LEDGER_ENTRY"),
    ):
        if record_id.startswith(prefix):
            return record_type
    raise AssertionError(f"unknown id {record_id}")


class TestAdversarialCases:
    def test_three_ambiguous_cases_with_anchor_evidence(self) -> None:
        result = reconcile(ingest_inputs(adversarial_inputs()).records)
        labels = load_labels("adversarial")
        assert len(result.cases) == 3
        runtime_evidence = {
            (
                case.category.value,
                tuple(sorted(f"{item.record_type}:{item.record_id}" for item in case.evidence)),
            )
            for case in result.cases
        }
        for label in labels["cases"]:
            anchors = tuple(
                sorted(f"{_type_of(item)}:{item}" for item in label["expected_evidence_ids"])
            )
            assert (label["expected_category"], anchors) in runtime_evidence

    def test_missing_bank_case_has_non_zero_bank_scoped_variance(self) -> None:
        result = reconcile(ingest_inputs(adversarial_inputs()).records)
        missing_bank = [
            case
            for case in result.cases
            if any(item.record_id == "stl_G5NU6VCxep" for item in case.evidence)
        ]
        assert len(missing_bank) == 1
        case = missing_bank[0]
        assert case.variance_paise < 0
        assert case.variance_scope == "BANK"
        assert case.affected_amount_paise == -case.variance_paise

    def test_composition_ambiguity_has_zero_variance_non_zero_affected(self) -> None:
        result = reconcile(ingest_inputs(adversarial_inputs()).records)
        composition = [
            case
            for case in result.cases
            if any(item.record_id == "pay_3UjY6a0SVK" for item in case.evidence)
        ]
        assert len(composition) == 1
        case = composition[0]
        assert case.variance_paise == 0
        assert case.affected_amount_paise == 2 * 14_634 * 100  # two aggregate rows
        assert case.variance_scope == "LEDGER"

    def test_all_accepted_rows_accounted(self) -> None:
        result = reconcile(ingest_inputs(adversarial_inputs()).records)
        assert result.unaccounted_record_keys == frozenset()
