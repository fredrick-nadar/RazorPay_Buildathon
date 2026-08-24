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


def apply_simulated_correction(
    db: Database,
    case_id: str,
    reviewer_id: str,
    action: ApprovalDecision | str = ApprovalDecision.APPROVED,
    notes: str = "",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Execute human approval or rejection on a verified exception case."""
    action_str = action.value if isinstance(action, ApprovalDecision) else str(action).upper()
    if action_str not in {ApprovalDecision.APPROVED.value, ApprovalDecision.REJECTED.value}:
        raise ValueError(f"invalid approval action: {action_str!r}")

    # 1. Fetch case
    case_row = db.query_one("SELECT * FROM cases WHERE case_id = ?", (case_id,))
    if case_row is None:
        raise ValueError(f"case {case_id!r} does not exist")

    # 2. Fetch latest proof if available
    proof_rows = db.query_all(
        "SELECT * FROM proofs WHERE case_id = ? ORDER BY rowid DESC LIMIT 1", (case_id,)
    )
    proof = proof_rows[0] if proof_rows else None
    proof_id = str(proof["proof_id"]) if proof is not None else "none"
    run_id = str(case_row["run_id"])

    # 3. Handle REJECTION
    if action_str == ApprovalDecision.REJECTED.value:
        approval_id = f"appr-{uuid4().hex[:12]}"
        now_utc = datetime.now(UTC).isoformat()

        with db.transaction():
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
            if proof is not None:
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
        }

    # 4. Handle APPROVAL & APPLICATION (Strictly requires verifier PASS)
    if proof is None:
        raise ValueError(f"case {case_id!r} has no verified proof package")

    if proof["verifier_status"] != "PASS":
        vstatus = proof["verifier_status"]
        raise ValueError(
            f"cannot approve case {case_id!r}: verifier status is {vstatus!r} (must be PASS)"
        )

    proof_hash = str(proof["canonical_hash"])

    # Fetch draft correction preview
    corr_rows = db.query_all(
        "SELECT * FROM corrections WHERE proof_id = ? ORDER BY rowid DESC LIMIT 1",
        (proof_id,),
    )
    if not corr_rows:
        raise ValueError(f"no dry-run correction exists for proof {proof_id!r}")
    corr = corr_rows[0]

    # 5. Handle APPROVAL & APPLICATION
    key = idempotency_key or f"simcorr|{case_id}|{proof_hash}"

    # Check for existing applied correction with this idempotency key
    existing = db.query_one("SELECT * FROM simulated_corrections WHERE idempotency_key = ?", (key,))
    if existing is not None:
        return SimulatedCorrectionResult(
            correction_id=str(existing["correction_id"]),
            case_id=str(existing["case_id"]),
            run_id=str(existing["run_id"]),
            proof_id=str(existing["proof_id"]),
            approval_id=str(existing["approval_id"]),
            status=CorrectionStatus.SIMULATED_APPLIED.value,
            target_ledger_entry_id=str(existing["target_ledger_entry_id"])
            if existing["target_ledger_entry_id"]
            else None,
            account_code=str(existing["account_code"]),
            delta_paise=int(existing["delta_paise"]),
            applied_at_utc=str(existing["applied_at_utc"]),
            reused=True,
            notes=notes,
        ).to_dict()

    approval_id = f"appr-{uuid4().hex[:12]}"
    sim_correction_id = f"simcorr-{uuid4().hex[:12]}"
    now_utc = datetime.now(UTC).isoformat()
    delta_paise = int(corr["proposed_delta_paise"])
    account_code = str(corr["account_code"]) if corr["account_code"] else "2100-MERCHANT-SETTLEMENT"
    target_ledger_id = (
        str(corr["target_ledger_entry_id"]) if corr["target_ledger_entry_id"] else None
    )

    with db.transaction():
        # Insert approval record
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

        # Insert simulated correction entry
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

        # Update case and draft correction statuses
        db.execute(
            "UPDATE cases SET status = ? WHERE case_id = ?",
            (CaseStatus.SIMULATED_APPLIED.value, case_id),
        )
        db.execute(
            "UPDATE corrections SET status = ? WHERE correction_id = ?",
            (CorrectionStatus.SIMULATED_APPLIED.value, str(corr["correction_id"])),
        )

        # Append audit record
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
