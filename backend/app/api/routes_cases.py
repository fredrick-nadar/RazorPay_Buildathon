"""Cases API routes for ARGUS CONTROL."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.audit.service import get_audit_trail
from app.corrections.application import (
    AuthorityConflictError,
    ProofIdentityError,
    apply_simulated_correction,
)
from app.domain.enums import ApprovalDecision
from app.graph.provenance import resolve_case_evidence_provenance
from app.persistence.database import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])


class ApprovalPayload(BaseModel):
    # The proof the reviewer actually saw. The dashboard already sent this
    # field and Pydantic discarded it, so an approval was applied against
    # whatever proof happened to be latest and a human could authorize a
    # proposal they never reviewed. It is now required and enforced.
    proof_id: str = Field(min_length=1, description="Proof the reviewer is deciding on")
    run_id: str = Field(min_length=1, description="Run containing the reviewed case")
    reviewer_id: str = Field(default="reviewer-finance-ops", description="Reviewer identifier")
    notes: str = Field(default="", description="Reviewer explanation or approval note")


def _require_case(db: Database, case_id: str, run_id: str | None) -> Any:
    """Resolve a case, optionally asserting it belongs to the selected run."""
    case_row = db.query_one("SELECT * FROM cases WHERE case_id = ?", (case_id,))
    if case_row is None:
        raise HTTPException(status_code=404, detail=f"case {case_id!r} not found")
    if run_id is not None and str(case_row["run_id"]) != run_id:
        # A cross-run request is a selection error, not an empty result.
        raise HTTPException(status_code=409, detail="CASE_RUN_MISMATCH")
    return case_row


@router.get("/{case_id}")
def get_case_detail(
    case_id: str,
    request: Request,
    run_id: str | None = Query(
        default=None, description="Assert the case belongs to this run before returning it"
    ),
) -> dict[str, Any]:
    db: Database = request.app.state.db

    case_row = _require_case(db, case_id, run_id)
    case_run_id = str(case_row["run_id"])

    # Evidence citations resolve to their immutable source rows, so the dossier
    # and the trace can show revision, hash and provenance, not just a bare id.
    evidence = [
        item.to_dict() for item in resolve_case_evidence_provenance(db, case_id, case_run_id)
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
            "run_id": case_run_id,
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


def _decide(
    db: Database,
    case_id: str,
    payload: ApprovalPayload,
    action: ApprovalDecision,
) -> dict[str, Any]:
    """Record one human authority decision, bound to the reviewed proof."""
    try:
        return apply_simulated_correction(
            db=db,
            case_id=case_id,
            reviewer_id=payload.reviewer_id,
            action=action,
            notes=payload.notes,
            expected_proof_id=payload.proof_id,
            expected_run_id=payload.run_id,
        )
    except ProofIdentityError as exc:
        # The reviewed proposal is no longer the current one. Refuse rather
        # than retarget the decision; the client must reload and decide again.
        raise HTTPException(status_code=409, detail="PROOF_SUPERSEDED") from exc
    except AuthorityConflictError as exc:
        raise HTTPException(status_code=409, detail="AUTHORITY_ALREADY_DECIDED") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Raw exception text never reaches a client; the log keeps the detail.
        logger.exception("authority decision failed for case %s", case_id)
        raise HTTPException(status_code=500, detail="AUTHORITY_DECISION_FAILED") from exc


@router.post("/{case_id}/approve")
def approve_case(case_id: str, payload: ApprovalPayload, request: Request) -> dict[str, Any]:
    return _decide(request.app.state.db, case_id, payload, ApprovalDecision.APPROVED)


@router.post("/{case_id}/reject")
def reject_case(case_id: str, payload: ApprovalPayload, request: Request) -> dict[str, Any]:
    return _decide(request.app.state.db, case_id, payload, ApprovalDecision.REJECTED)


@router.get("/{case_id}/audit")
def get_case_audit(
    case_id: str,
    request: Request,
    run_id: str | None = Query(
        default=None, description="Assert the case belongs to this run before returning events"
    ),
) -> list[dict[str, Any]]:
    """Return this case's append-only events in authoritative order.

    Fails closed on an unknown case, and on a case that does not belong to the
    asserted run, so an empty trail always means "no events recorded".
    """
    db: Database = request.app.state.db
    case_row = _require_case(db, case_id, run_id)
    events = get_audit_trail(db=db, case_id=case_id, run_id=str(case_row["run_id"]))
    return [e.to_dict() for e in events]
