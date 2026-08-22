"""Phase 3 verifier, proof, and dry-run behavior over committed datasets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.corrections.dry_run import CorrectionRefused, preview_correction
from app.domain.enums import CaseStatus, VerifierStatus
from app.importers.ingest import ingest_inputs
from app.persistence.database import Database
from app.reconciliation.detectors import reconcile
from app.runs import execute_run
from app.verifier.engine import build_system_hypotheses, verify_case, verify_cases
from app.verifier.proof import proof_is_complete, proof_stale_reasons
from app.verifier.snapshot import build_evidence_snapshot

REPO_ROOT = Path(__file__).resolve().parents[3]


def _run(profile: str, tmp_path: Path) -> tuple[dict, Database]:
    database = Database(tmp_path / f"{profile}-phase3.sqlite3")
    result = execute_run(REPO_ROOT / "datasets" / profile / "inputs", database)
    return dict(result.summary), database


def _case_key(case: dict) -> tuple[str, tuple[str, ...]]:
    return (
        case["category"],
        tuple(sorted(f"{item['record_type']}:{item['record_id']}" for item in case["evidence"])),
    )


def _label_key(label: dict) -> tuple[str, tuple[str, ...]]:
    prefixes = {
        "pay_": "PAYMENT",
        "rfd_": "REFUND",
        "stl_": "SETTLEMENT",
        "bnk_": "BANK_ENTRY",
        "led_": "LEDGER_ENTRY",
    }

    def record_type(record_id: str) -> str:
        return next(kind for prefix, kind in prefixes.items() if record_id.startswith(prefix))

    return (
        label["expected_category"],
        tuple(
            sorted(
                f"{record_type(str(record_id))}:{record_id}"
                for record_id in label["expected_evidence_ids"]
            )
        ),
    )


class TestPhase3VerificationOutcomes:
    def test_dev_outcomes_and_deltas_match_labels(self, tmp_path: Path) -> None:
        summary, database = _run("dev", tmp_path)
        try:
            labels = json.loads(
                (REPO_ROOT / "datasets" / "dev" / "labels" / "labels.json").read_text(
                    encoding="utf-8"
                )
            )
            labels_by_key = {_label_key(label): label for label in labels["cases"]}
            assert len(labels_by_key) == 12

            for case in summary["cases"]:
                label = labels_by_key[_case_key(case)]
                assert case["status"] == label["expected_outcome"]
                assert case["proposed_delta_paise"] == label["expected_delta_paise"]

            assert summary["verification"]["case_status_counts"] == {
                "APPROVAL_REQUIRED": 6,
                "UNRESOLVED": 3,
                "VERIFIED_RESOLVED": 3,
            }
            assert summary["verification"]["passing_proof_completeness"] == {
                "denominator": 9,
                "numerator": 9,
            }
            assert summary["verification"]["dry_run_abs_variance_after_paise"] == 0
            assert summary["verification"]["dry_run_count"] == 9

            proof_rows = database.query_all("SELECT * FROM proofs")
            correction_rows = database.query_all("SELECT * FROM corrections")
            assert len(proof_rows) == 12
            assert len(correction_rows) == 9
            assert {row["status"] for row in correction_rows} == {"DRAFT"}
            assert (
                database.query_one(
                    "SELECT COUNT(*) AS c FROM norm_ledger_entries "
                    "WHERE entry_origin = 'SIMULATED_CORRECTION'"
                )["c"]
                == 0
            )
        finally:
            database.close()

    def test_adversarial_ambiguity_never_passes(self, tmp_path: Path) -> None:
        summary, database = _run("adversarial", tmp_path)
        try:
            assert summary["verification"]["verifier_status_counts"] == {
                "FAIL": 0,
                "INCONCLUSIVE": 3,
                "PASS": 0,
            }
            assert {case["status"] for case in summary["cases"]} == {CaseStatus.UNRESOLVED.value}
            assert summary["verification"]["dry_run_count"] == 0
            assert database.query_all("SELECT * FROM corrections") == []
        finally:
            database.close()


class TestVerifierMutations:
    def _first_case_outcome(self, profile: str):
        ingest = ingest_inputs(REPO_ROOT / "datasets" / profile / "inputs")
        result = reconcile(ingest.records)
        snapshot = build_evidence_snapshot(ingest.records)
        hypotheses = build_system_hypotheses(list(result.cases))
        return ingest.records, result, snapshot, hypotheses

    def test_unknown_evidence_id_fails_safe(self) -> None:
        _records, result, snapshot, hypotheses = self._first_case_outcome("dev")
        case = result.cases[0]
        hypothesis = hypotheses[0]
        mutated = hypothesis.__class__(
            hypothesis_id=hypothesis.hypothesis_id,
            case_id=hypothesis.case_id,
            category=hypothesis.category,
            claim="claim text cannot rescue an unknown id",
            evidence_ids=(*hypothesis.evidence_ids, "PAYMENT:missing_record"),
        )
        verified = verify_case(case, mutated, snapshot)
        assert verified.status == VerifierStatus.FAIL
        assert "UNKNOWN_EVIDENCE_ID" in verified.reason_codes

    def test_consumed_refund_blocks_second_missing_refund_claim_only(self) -> None:
        _records, result, snapshot, hypotheses = self._first_case_outcome("dev")
        missing = [
            (case, hypothesis)
            for case, hypothesis in zip(result.cases, hypotheses, strict=True)
            if case.category.value == "MISSING_REFUND_POSTING"
        ]
        case, hypothesis = missing[0]
        refund_key = next(
            (item.record_type, item.record_id)
            for item in case.evidence
            if item.record_type == "REFUND"
        )
        verified = verify_case(case, hypothesis, snapshot, frozenset({refund_key}))
        assert verified.status == VerifierStatus.FAIL
        assert "RECORD_ALREADY_CONSUMED" in verified.reason_codes

    def test_ambiguity_claim_text_cannot_pass(self) -> None:
        records, result, _snapshot, _hypotheses = self._first_case_outcome("adversarial")
        outcome = verify_cases(records, list(result.cases))
        assert all(
            item.result.status == VerifierStatus.INCONCLUSIVE for item in outcome.verifications
        )
        assert all(item.case.status == CaseStatus.UNRESOLVED for item in outcome.verifications)

    def test_dry_run_refuses_non_pass_proof_and_stale_proof_is_detected(self) -> None:
        records, result, _snapshot, _hypotheses = self._first_case_outcome("adversarial")
        outcome = verify_cases(records, list(result.cases))
        item = outcome.verifications[0]
        snapshot = build_evidence_snapshot(records)
        with pytest.raises(CorrectionRefused):
            preview_correction(item.case, item.proof, snapshot, item.authority)
        assert not proof_is_complete(item.proof)
        assert proof_stale_reasons(
            item.proof,
            current_verifier_manifest={"changed": {"version": "x"}},
        )
