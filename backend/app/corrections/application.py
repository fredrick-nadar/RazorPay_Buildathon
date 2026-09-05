"""Simulated correction application (PRD 6.11, 11.2, 11.3, 16).

Applies verified and approved corrections as new linked ``SIMULATED_CORRECTION``
entries in the persistence layer. Raw imported source rows remain 100% immutable.
Idempotency is cryptographically enforced via proof canonical hashes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.audit.service import record_audit_event
from app.domain.enums import ActorType, ApprovalDecision, CaseStatus, CorrectionStatus
from app.persistence.database import Database


class ProofIdentityError(ValueError):
    """The authority decision does not name the proof that is current for the case.

    Human approval is authority over one specific verified proposal. If the
    reviewed proof has been superseded, or the caller names a different proof,
    the decision must not be silently retargeted onto whatever proof is latest.
    """


class AuthorityConflictError(ValueError):
    """The reviewed proof already has a different final human decision."""


@dataclass(frozen=True)
class SimulatedCorrectionResult:
    correction_id: str
    case_id: str
    run_id: str
    proof_id: str
    approval_id: str
    status: str
    target_ledger_entry_id: str | None
    account_code: str
    delta_paise: int
    applied_at_utc: str
    reused: bool
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _find_applied(db: Database, idempotency_key: str) -> Any:
    """Return the already-applied simulated correction for this key, or None."""
    return db.query_one(
        "SELECT * FROM simulated_corrections WHERE idempotency_key = ?", (idempotency_key,)
    )


def _reuse(row: Any, notes: str) -> dict[str, Any]:
    """Describe an existing simulated correction without creating another."""
    return SimulatedCorrectionResult(
        correction_id=str(row["correction_id"]),
        case_id=str(row["case_id"]),
        run_id=str(row["run_id"]),
        proof_id=str(row["proof_id"]),
        approval_id=str(row["approval_id"]),
        status=CorrectionStatus.SIMULATED_APPLIED.value,
        target_ledger_entry_id=(
            str(row["target_ledger_entry_id"]) if row["target_ledger_entry_id"] else None
        ),
        account_code=str(row["account_code"]),
        delta_paise=int(row["delta_paise"]),
        applied_at_utc=str(row["applied_at_utc"]),
        reused=True,
        notes=notes,
    ).to_dict()


def apply_simulated_correction(
    db: Database,
    case_id: str,
    reviewer_id: str,
    action: ApprovalDecision | str = ApprovalDecision.APPROVED,
    notes: str = "",
    *,
    expected_proof_id: str,
    expected_run_id: str,
) -> dict[str, Any]:
    """Execute human approval or rejection on a verified exception case.

    The proof and run are mandatory: internal callers cannot bypass the same
    authority binding enforced at the HTTP boundary. The entire read/check/
    write transition runs under one immediate transaction, so another process
    cannot supersede the proof or apply a competing decision between checks.
    """
    action_str = action.value if isinstance(action, ApprovalDecision) else str(action).upper()
    if action_str not in {ApprovalDecision.APPROVED.value, ApprovalDecision.REJECTED.value}:
        raise ValueError(f"invalid approval action: {action_str!r}")

    with db.transaction(immediate=True):
        case_row = db.query_one("SELECT * FROM cases WHERE case_id = ?", (case_id,))
        if case_row is None:
            raise ValueError(f"case {case_id!r} does not exist")

        run_id = str(case_row["run_id"])
        if expected_run_id != run_id:
            raise ProofIdentityError(
                f"case {case_id!r} belongs to run {run_id!r}, not {expected_run_id!r}"
            )

        proof = db.query_one(
            "SELECT * FROM proofs WHERE case_id = ? ORDER BY rowid DESC LIMIT 1", (case_id,)
        )
        if proof is None or str(proof["proof_id"]) != expected_proof_id:
            raise ProofIdentityError(
                f"proof {expected_proof_id!r} is not current for case {case_id!r}"
            )
        proof_id = str(proof["proof_id"])

        applied = db.query_one(
            "SELECT * FROM simulated_corrections WHERE case_id = ? AND proof_id = ?",
            (case_id, proof_id),
        )
        prior_decision = db.query_one(
            "SELECT * FROM approvals WHERE case_id = ? AND proof_id = ? ORDER BY rowid ASC LIMIT 1",
            (case_id, proof_id),
        )

        if applied is not None:
            if action_str == ApprovalDecision.APPROVED.value:
                return _reuse(applied, notes)
            raise AuthorityConflictError("an applied correction cannot later be rejected")

        if prior_decision is not None:
            prior_action = str(prior_decision["action"])
            if action_str == prior_action == ApprovalDecision.REJECTED.value:
                return {
                    "status": "REJECTED",
                    "case_id": case_id,
                    "approval_id": str(prior_decision["approval_id"]),
                    "reviewer_id": str(prior_decision["reviewer_id"]),
                    "notes": str(prior_decision["notes"] or ""),
                    "applied": False,
                    "reused": True,
                }
            raise AuthorityConflictError("this proof already has a final human decision")

        if str(case_row["status"]) != CaseStatus.APPROVAL_REQUIRED.value:
            raise AuthorityConflictError("case is not awaiting an authority decision")

        now_utc = datetime.now(UTC).isoformat()

        if action_str == ApprovalDecision.REJECTED.value:
            approval_id = f"appr-{uuid4().hex[:12]}"
            db.execute(
                "INSERT INTO approvals ("
                "approval_id, case_id, proof_id, reviewer_id, action, notes, approved_at_utc"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (approval_id, case_id, proof_id, reviewer_id, action_str, notes, now_utc),
            )
            db.execute(
                "UPDATE cases SET status = ? WHERE case_id = ?",
                (CaseStatus.UNRESOLVED.value, case_id),
            )
            db.execute(
                "UPDATE corrections SET status = ? WHERE proof_id = ?",
                (CorrectionStatus.REJECTED.value, proof_id),
            )
            record_audit_event(
                db=db,
                actor=ActorType.USER,
                action="CASE_REJECTED_BY_HUMAN",
                case_id=case_id,
                run_id=run_id,
                payload={
                    "approval_id": approval_id,
                    "reviewer_id": reviewer_id,
                    "notes": notes,
                    "proof_id": proof_id,
                },
            )

            return {
                "status": "REJECTED",
                "case_id": case_id,
                "approval_id": approval_id,
                "reviewer_id": reviewer_id,
                "notes": notes,
                "applied": False,
                "reused": False,
            }

        if proof["verifier_status"] != "PASS":
            raise ValueError(f"cannot approve case {case_id!r}: verifier status must be PASS")

        corr = db.query_one(
            "SELECT * FROM corrections WHERE proof_id = ? ORDER BY rowid DESC LIMIT 1",
            (proof_id,),
        )
        if corr is None:
            raise ValueError(f"no dry-run correction exists for proof {proof_id!r}")

        key = f"simcorr|{case_id}|{proof['canonical_hash']}"
        existing = _find_applied(db, key)
        if existing is not None:
            return _reuse(existing, notes)

        approval_id = f"appr-{uuid4().hex[:12]}"
        sim_correction_id = f"simcorr-{uuid4().hex[:12]}"
        delta_paise = int(corr["proposed_delta_paise"])
        account_code = (
            str(corr["account_code"]) if corr["account_code"] else "2100-MERCHANT-SETTLEMENT"
        )
        target_ledger_id = (
            str(corr["target_ledger_entry_id"]) if corr["target_ledger_entry_id"] else None
        )

        db.execute(
            "INSERT INTO approvals ("
            "approval_id, case_id, proof_id, reviewer_id, action, notes, approved_at_utc"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (approval_id, case_id, proof_id, reviewer_id, action_str, notes, now_utc),
        )

        record_audit_event(
            db=db,
            actor=ActorType.USER,
            action="APPROVAL_SUBMITTED",
            case_id=case_id,
            run_id=run_id,
            payload={
                "approval_id": approval_id,
                "reviewer_id": reviewer_id,
                "proof_id": proof_id,
                "notes": notes,
            },
        )

        db.execute(
            "INSERT INTO simulated_corrections ("
            "correction_id, case_id, run_id, proof_id, approval_id, target_ledger_entry_id, "
            "account_code, delta_paise, applied_at_utc, idempotency_key"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sim_correction_id,
                case_id,
                run_id,
                proof_id,
                approval_id,
                target_ledger_id,
                account_code,
                delta_paise,
                now_utc,
                key,
            ),
        )

        db.execute(
            "UPDATE cases SET status = ? WHERE case_id = ?",
            (CaseStatus.SIMULATED_APPLIED.value, case_id),
        )
        db.execute(
            "UPDATE corrections SET status = ? WHERE correction_id = ?",
            (CorrectionStatus.SIMULATED_APPLIED.value, str(corr["correction_id"])),
        )

        record_audit_event(
            db=db,
            actor=ActorType.SYSTEM,
            action="SIMULATED_CORRECTION_APPLIED",
            case_id=case_id,
            run_id=run_id,
            payload={
                "correction_id": sim_correction_id,
                "approval_id": approval_id,
                "target_ledger_entry_id": target_ledger_id,
                "account_code": account_code,
                "delta_paise": delta_paise,
                "idempotency_key": key,
            },
        )

    return SimulatedCorrectionResult(
        correction_id=sim_correction_id,
        case_id=case_id,
        run_id=run_id,
        proof_id=proof_id,
        approval_id=approval_id,
        status=CorrectionStatus.SIMULATED_APPLIED.value,
        target_ledger_entry_id=target_ledger_id,
        account_code=account_code,
        delta_paise=delta_paise,
        applied_at_utc=now_utc,
        reused=False,
        notes=notes,
    ).to_dict()
