"""Unit tests for simulated correction application and human approval."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.corrections.application import apply_simulated_correction
from app.domain.enums import ApprovalDecision, CaseStatus, CorrectionStatus
from app.persistence.database import Database


def _seed_verified_case(db: Database, case_id: str = "case-001", delta_paise: int = 50000) -> None:
    now_utc = "2026-08-24T12:00:00Z"
    with db.transaction():
        db.execute(
            "INSERT INTO runs (run_id, idempotency_key, tenant_id, inputs_path, "
            "inputs_fingerprint, status, started_at_utc, summary_json, rule_manifest_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("run-001", "key-001", "tenant-001", "inputs", "fp", "COMPLETED", now_utc, "{}", "{}"),
        )
        db.execute(
            "INSERT INTO cases (case_id, run_id, category_candidate, status, variance_paise, "
            "affected_amount_paise, proposed_delta_paise, currency, summary, reason_codes_json, "
            "opened_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case_id,
                "run-001",
                "DUPLICATE_LEDGER_POSTING",
                "APPROVAL_REQUIRED",
                50000,
                50000,
                delta_paise,
                "INR",
                "Duplicate ledger posting",
                "[]",
                now_utc,
                now_utc,
            ),
        )
        db.execute(
            "INSERT INTO hypotheses (hypothesis_id, case_id, category, claim, evidence_json, "
            "status, reason_codes_json, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "hypo-001",
                case_id,
                "DUPLICATE_LEDGER_POSTING",
                "Duplicate ledger row found",
                "[]",
                "SUPPORTED",
                "[]",
                now_utc,
            ),
        )
        db.execute(
            "INSERT INTO proofs (proof_id, case_id, hypothesis_id, claim, category, "
            "evidence_json, supported_evidence_json, conflicting_evidence_json, equations_json, "
            "rejected_alternatives_json, verifier_status, verifier_rule_id, "
            "verifier_rule_version, recon_manifest_fingerprint, verifier_manifest_fingerprint, "
            "proposed_delta_paise, authority_decision, requires_approval, uncertainty_json, "
            "competing_candidates_json, canonical_hash, created_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "proof-001",
                case_id,
                "hypo-001",
                "Claim",
                "DUPLICATE_LEDGER_POSTING",
                "[]",
                "[]",
                "[]",
                "[]",
                "[]",
                "PASS",
                "rule-dup",
                "v1",
                "fp1",
                "fp2",
                delta_paise,
                "APPROVAL_REQUIRED",
                1,
                "[]",
                "[]",
                "hash-proof-123",
                now_utc,
            ),
        )
        db.execute(
            "INSERT INTO corrections (correction_id, case_id, proof_id, status, "
            "target_ledger_entry_id, account_code, proposed_delta_paise, variance_before_paise, "
            "variance_after_paise, totals_before_json, totals_after_json, warnings_json, "
            "uncertainty_json, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "corr-draft-001",
                case_id,
                "proof-001",
                "DRAFT",
                "led-001",
                "2100-MERCHANT-SETTLEMENT",
                delta_paise,
                50000,
                0,
                "{}",
                "{}",
                "[]",
                "[]",
                now_utc,
            ),
        )


def test_apply_simulated_correction_approve_and_idempotence(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    try:
        _seed_verified_case(db, case_id="case-001", delta_paise=50000)

        # 1. Approve
        res = apply_simulated_correction(
            db=db,
            case_id="case-001",
            reviewer_id="rev-john",
            action=ApprovalDecision.APPROVED,
            notes="Approved after verifying duplicate row",
        )
        assert res["status"] == CorrectionStatus.SIMULATED_APPLIED.value
        assert res["reused"] is False
        assert res["delta_paise"] == 50000

        # Check DB state
        case_row = db.query_one("SELECT status FROM cases WHERE case_id = 'case-001'")
        assert case_row is not None and case_row["status"] == CaseStatus.SIMULATED_APPLIED.value

        sim_rows = db.query_all("SELECT * FROM simulated_corrections WHERE case_id = 'case-001'")
        assert len(sim_rows) == 1

        # 2. Re-apply (Idempotency)
        res2 = apply_simulated_correction(
            db=db,
            case_id="case-001",
            reviewer_id="rev-john",
            action=ApprovalDecision.APPROVED,
        )
        assert res2["reused"] is True
        assert res2["correction_id"] == res["correction_id"]
        # Still only 1 entry in DB
        assert len(db.query_all("SELECT * FROM simulated_corrections")) == 1
    finally:
        db.close()


def test_apply_simulated_correction_reject(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    try:
        _seed_verified_case(db, case_id="case-002", delta_paise=25000)

        res = apply_simulated_correction(
            db=db,
            case_id="case-002",
            reviewer_id="rev-sarah",
            action=ApprovalDecision.REJECTED,
            notes="Rejected: need more merchant ledger documentation",
        )
        assert res["status"] == "REJECTED"
        assert res["applied"] is False

        case_row = db.query_one("SELECT status FROM cases WHERE case_id = 'case-002'")
        assert case_row is not None and case_row["status"] == CaseStatus.UNRESOLVED.value

        # No simulated corrections created
        sim_rows = db.query_all("SELECT * FROM simulated_corrections WHERE case_id = 'case-002'")
        assert len(sim_rows) == 0
    finally:
        db.close()


def test_apply_unverified_case_raises(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    try:
        now_utc = "2026-08-24T12:00:00Z"
        with db.transaction():
            db.execute(
                "INSERT INTO runs (run_id, idempotency_key, tenant_id, inputs_path, "
                "inputs_fingerprint, status, started_at_utc, summary_json, rule_manifest_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "run-001",
                    "key-001",
                    "tenant-001",
                    "inputs",
                    "fp",
                    "COMPLETED",
                    now_utc,
                    "{}",
                    "{}",
                ),
            )
            db.execute(
                "INSERT INTO cases (case_id, run_id, category_candidate, status, variance_paise, "
                "affected_amount_paise, proposed_delta_paise, currency, summary, "
                "reason_codes_json, opened_at_utc, updated_at_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "case-inconcl",
                    "run-001",
                    "AMBIGUOUS_EVIDENCE",
                    "UNRESOLVED",
                    50000,
                    50000,
                    0,
                    "INR",
                    "Ambiguous evidence",
                    "[]",
                    now_utc,
                    now_utc,
                ),
            )
            db.execute(
                "INSERT INTO proofs (proof_id, case_id, hypothesis_id, claim, category, "
                "evidence_json, supported_evidence_json, conflicting_evidence_json, "
                "equations_json, rejected_alternatives_json, verifier_status, verifier_rule_id, "
                "verifier_rule_version, recon_manifest_fingerprint, verifier_manifest_fingerprint, "
                "proposed_delta_paise, authority_decision, requires_approval, uncertainty_json, "
                "competing_candidates_json, canonical_hash, created_at_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "proof-inconcl",
                    "case-inconcl",
                    "hypo-none",
                    "Claim",
                    "AMBIGUOUS_EVIDENCE",
                    "[]",
                    "[]",
                    "[]",
                    "[]",
                    "[]",
                    "INCONCLUSIVE",
                    "rule-ambig",
                    "v1",
                    "fp1",
                    "fp2",
                    None,
                    "UNRESOLVED",
                    0,
                    "[]",
                    "[]",
                    "hash-inconcl",
                    now_utc,
                ),
            )

        with pytest.raises(ValueError, match="must be PASS"):
            apply_simulated_correction(
                db=db,
                case_id="case-inconcl",
                reviewer_id="rev-bad",
                action=ApprovalDecision.APPROVED,
            )
    finally:
        db.close()
