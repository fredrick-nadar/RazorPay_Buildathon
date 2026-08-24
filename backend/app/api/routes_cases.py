"""Cases API routes for ARGUS CONTROL."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.audit.service import get_audit_trail
from app.corrections.application import apply_simulated_correction
from app.domain.enums import ApprovalDecision
from app.persistence.database import Database

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])


class ApprovalPayload(BaseModel):
    reviewer_id: str = Field(default="reviewer-finance-ops", description="Reviewer identifier")
    notes: str = Field(default="", description="Reviewer explanation or approval note")


@router.get("/{case_id}")
def get_case_detail(case_id: str, request: Request) -> dict[str, Any]:
    db: Database = request.app.state.db

    case_row = db.query_one("SELECT * FROM cases WHERE case_id = ?", (case_id,))
    if case_row is None:
        raise HTTPException(status_code=404, detail=f"case {case_id!r} not found")

    evidence_rows = db.query_all(
        "SELECT record_type, record_id, note FROM case_evidence WHERE case_id = ?", (case_id,)
    )
    evidence = [
        {
            "record_type": str(e["record_type"]),
            "record_id": str(e["record_id"]),
            "note": str(e["note"]) if e["note"] else None,
        }
        for e in evidence_rows
    ]

    hypotheses_rows = db.query_all(
        "SELECT * FROM hypotheses WHERE case_id = ? ORDER BY rowid ASC", (case_id,)
    )
    hypotheses = [
        {
            "hypothesis_id": str(h["hypothesis_id"]),
            "category": str(h["category"]),
            "claim": str(h["claim"]),
            "evidence": json.loads(str(h["evidence_json"])),
            "status": str(h["status"]),
            "reason_codes": json.loads(str(h["reason_codes_json"])),
            "created_at_utc": str(h["created_at_utc"]),
        }
        for h in hypotheses_rows
    ]

    proof_row = db.query_one(
        "SELECT * FROM proofs WHERE case_id = ? ORDER BY rowid DESC LIMIT 1", (case_id,)
    )
    proof = None
    if proof_row is not None:
        proof = {
            "proof_id": str(proof_row["proof_id"]),
            "hypothesis_id": str(proof_row["hypothesis_id"]),
            "claim": str(proof_row["claim"]),
            "category": str(proof_row["category"]),
            "evidence": json.loads(str(proof_row["evidence_json"])),
            "supported_evidence": json.loads(str(proof_row["supported_evidence_json"])),
            "conflicting_evidence": json.loads(str(proof_row["conflicting_evidence_json"])),
            "equations": json.loads(str(proof_row["equations_json"])),
            "rejected_alternatives": json.loads(str(proof_row["rejected_alternatives_json"])),
            "verifier_status": str(proof_row["verifier_status"]),
            "verifier_rule_id": str(proof_row["verifier_rule_id"]),
            "verifier_rule_version": str(proof_row["verifier_rule_version"]),
            "proposed_delta_paise": int(proof_row["proposed_delta_paise"])
            if proof_row["proposed_delta_paise"] is not None
            else None,
            "authority_decision": str(proof_row["authority_decision"]),
            "requires_approval": bool(proof_row["requires_approval"]),
            "uncertainty": json.loads(str(proof_row["uncertainty_json"])),
            "competing_candidates": json.loads(str(proof_row["competing_candidates_json"])),
            "canonical_hash": str(proof_row["canonical_hash"]),
            "created_at_utc": str(proof_row["created_at_utc"]),
        }

    corr_row = db.query_one(
        "SELECT * FROM corrections WHERE case_id = ? ORDER BY rowid DESC LIMIT 1", (case_id,)
    )
    dry_run = None
    if corr_row is not None:
        dry_run = {
            "correction_id": str(corr_row["correction_id"]),
            "proof_id": str(corr_row["proof_id"]),
            "status": str(corr_row["status"]),
            "proposed_entry": json.loads(str(corr_row["proposed_entry_json"]))
            if corr_row["proposed_entry_json"]
            else None,
            "target_ledger_entry_id": str(corr_row["target_ledger_entry_id"])
            if corr_row["target_ledger_entry_id"]
            else None,
            "account_code": str(corr_row["account_code"]) if corr_row["account_code"] else None,
            "proposed_delta_paise": int(corr_row["proposed_delta_paise"]),
            "variance_before_paise": int(corr_row["variance_before_paise"]),
            "variance_after_paise": int(corr_row["variance_after_paise"]),
            "totals_before_paise": json.loads(str(corr_row["totals_before_json"])),
            "totals_after_paise": json.loads(str(corr_row["totals_after_json"])),
            "warnings": json.loads(str(corr_row["warnings_json"])),
            "uncertainty": json.loads(str(corr_row["uncertainty_json"])),
            "created_at_utc": str(corr_row["created_at_utc"]),
        }

    sim_row = db.query_one(
        "SELECT * FROM simulated_corrections WHERE case_id = ? ORDER BY rowid DESC LIMIT 1",
        (case_id,),
    )
    simulated_correction = None
    if sim_row is not None:
        simulated_correction = {
            "correction_id": str(sim_row["correction_id"]),
            "case_id": str(sim_row["case_id"]),
            "run_id": str(sim_row["run_id"]),
            "proof_id": str(sim_row["proof_id"]),
            "approval_id": str(sim_row["approval_id"]),
            "target_ledger_entry_id": str(sim_row["target_ledger_entry_id"])
            if sim_row["target_ledger_entry_id"]
            else None,
            "account_code": str(sim_row["account_code"]),
            "delta_paise": int(sim_row["delta_paise"]),
            "applied_at_utc": str(sim_row["applied_at_utc"]),
            "idempotency_key": str(sim_row["idempotency_key"]),
        }

    approvals_rows = db.query_all(
        "SELECT * FROM approvals WHERE case_id = ? ORDER BY rowid ASC", (case_id,)
    )
    approvals = [
        {
            "approval_id": str(a["approval_id"]),
            "proof_id": str(a["proof_id"]),
            "reviewer_id": str(a["reviewer_id"]),
            "action": str(a["action"]),
            "notes": str(a["notes"]) if a["notes"] else None,
            "approved_at_utc": str(a["approved_at_utc"]),
        }
        for a in approvals_rows
    ]

    return {
        "case": {
            "case_id": case_id,
            "run_id": str(case_row["run_id"]),
            "category": str(case_row["category_candidate"]),
            "status": str(case_row["status"]),
            "variance_paise": int(case_row["variance_paise"]),
            "affected_amount_paise": int(case_row["affected_amount_paise"]),
            "proposed_delta_paise": int(case_row["proposed_delta_paise"])
            if case_row["proposed_delta_paise"] is not None
            else None,
            "currency": str(case_row["currency"]),
            "summary": str(case_row["summary"]),
            "reason_codes": json.loads(str(case_row["reason_codes_json"])),
            "evidence": evidence,
            "opened_at_utc": str(case_row["opened_at_utc"]),
            "updated_at_utc": str(case_row["updated_at_utc"]),
        },
        "hypotheses": hypotheses,
        "proof": proof,
        "dry_run": dry_run,
        "simulated_correction": simulated_correction,
        "approvals": approvals,
    }


@router.post("/{case_id}/approve")
def approve_case(case_id: str, payload: ApprovalPayload, request: Request) -> dict[str, Any]:
    db: Database = request.app.state.db
    try:
        res = apply_simulated_correction(
            db=db,
            case_id=case_id,
            reviewer_id=payload.reviewer_id,
            action=ApprovalDecision.APPROVED,
            notes=payload.notes,
        )
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{case_id}/reject")
def reject_case(case_id: str, payload: ApprovalPayload, request: Request) -> dict[str, Any]:
    db: Database = request.app.state.db
    try:
        res = apply_simulated_correction(
            db=db,
            case_id=case_id,
            reviewer_id=payload.reviewer_id,
            action=ApprovalDecision.REJECTED,
            notes=payload.notes,
        )
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{case_id}/audit")
def get_case_audit(case_id: str, request: Request) -> list[dict[str, Any]]:
    db: Database = request.app.state.db
    events = get_audit_trail(db=db, case_id=case_id)
    return [e.to_dict() for e in events]
