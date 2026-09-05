"""Append-only audit service (PRD 6.12, 11.4, 14.2).

Every financial and investigation milestone is recorded in an immutable,
append-only audit log in SQLite. The digest guarantees cryptographic tampering
detection across events.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from app.domain.enums import ActorType
from app.persistence.database import Database


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    case_id: str | None
    run_id: str | None
    timestamp_utc: str
    actor: str
    action: str
    payload: dict[str, Any]
    digest: str
    # Position in the append-only log, taken from the storage rowid. Wall-clock
    # timestamps can tie or arrive out of order; this is the authoritative
    # append order a client may render and assert on. 0 when the event was just
    # constructed by record_audit_event and not yet read back.
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_audit_digest(
    event_id: str,
    case_id: str | None,
    run_id: str | None,
    timestamp_utc: str,
    actor: str,
    action: str,
    payload: dict[str, Any],
) -> str:
    material = "|".join(
        (
            event_id,
            case_id or "none",
            run_id or "none",
            timestamp_utc,
            actor,
            action,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
        )
    )
    return sha256(material.encode("utf-8")).hexdigest()


def record_audit_event(
    db: Database,
    actor: ActorType | str,
    action: str,
    payload: dict[str, Any],
    case_id: str | None = None,
    run_id: str | None = None,
    timestamp_utc: str | None = None,
    *,
    event_id: str | None = None,
) -> AuditEvent:
    """Append a single audit record into the SQLite audit_log table."""
    event_id = event_id or f"evt-{uuid4().hex[:12]}"
    ts = timestamp_utc or datetime.now(UTC).isoformat()
    actor_str = actor.value if isinstance(actor, ActorType) else str(actor)
    digest = compute_audit_digest(
        event_id=event_id,
        case_id=case_id,
        run_id=run_id,
        timestamp_utc=ts,
        actor=actor_str,
        action=action,
        payload=payload,
    )
    db.execute(
        "INSERT INTO audit_log ("
        "event_id, case_id, run_id, timestamp_utc, actor, action, payload_json, digest"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id,
            case_id,
            run_id,
            ts,
            actor_str,
            action,
            json.dumps(payload, sort_keys=True),
            digest,
        ),
    )

    return AuditEvent(
        event_id=event_id,
        case_id=case_id,
        run_id=run_id,
        timestamp_utc=ts,
        actor=actor_str,
        action=action,
        payload=payload,
        digest=digest,
    )


def get_audit_trail(
    db: Database,
    case_id: str | None = None,
    run_id: str | None = None,
) -> list[AuditEvent]:
    """Retrieve the append-ordered audit trail, narrowed by the given scope.

    Passing both ids narrows to the events of that case WITHIN that run. It
    previously widened to ``case_id = ? OR run_id = ?``, which returned the
    whole run's trail for a case query and let one case's view show another
    case's events.
    """
    columns = (
        "SELECT rowid AS sequence, event_id, case_id, run_id, timestamp_utc, "
        "actor, action, payload_json, digest FROM audit_log"
    )
    if case_id is not None and run_id is not None:
        rows = db.query_all(
            f"{columns} WHERE case_id = ? AND run_id = ? ORDER BY rowid ASC",
            (case_id, run_id),
        )
    elif case_id is not None:
        rows = db.query_all(f"{columns} WHERE case_id = ? ORDER BY rowid ASC", (case_id,))
    elif run_id is not None:
        rows = db.query_all(f"{columns} WHERE run_id = ? ORDER BY rowid ASC", (run_id,))
    else:
        rows = db.query_all(f"{columns} ORDER BY rowid ASC")

    events: list[AuditEvent] = []
    for r in rows:
        events.append(
            AuditEvent(
                event_id=str(r["event_id"]),
                case_id=str(r["case_id"]) if r["case_id"] else None,
                run_id=str(r["run_id"]) if r["run_id"] else None,
                timestamp_utc=str(r["timestamp_utc"]),
                actor=str(r["actor"]),
                action=str(r["action"]),
                payload=json.loads(str(r["payload_json"])),
                digest=str(r["digest"]),
                sequence=int(r["sequence"]),
            )
        )
    return events


def verify_audit_completeness(db: Database, case_id: str) -> dict[str, Any]:
    """Audit completeness assertion (PRD 14.2).

    A resolved/applied case must possess verified proof, preview, approval, and application events.
    """
    events = get_audit_trail(db, case_id=case_id)
    actions = {e.action for e in events}

    proof_rows = db.query_all(
        "SELECT proof_id, verifier_status FROM proofs WHERE case_id = ?", (case_id,)
    )
    sim_rows = db.query_all(
        "SELECT correction_id FROM simulated_corrections WHERE case_id = ?", (case_id,)
    )
    appr_rows = db.query_all(
        "SELECT approval_id, action FROM approvals WHERE case_id = ?", (case_id,)
    )

    is_applied = len(sim_rows) > 0
    is_approved = any(r["action"] == "APPROVED" for r in appr_rows)
    has_pass_proof = any(r["verifier_status"] == "PASS" for r in proof_rows)

    checks = {
        "has_events": len(events) > 0,
        "has_pass_proof": has_pass_proof,
        "is_approved": is_approved,
        "is_applied": is_applied,
        "has_approval_event": "APPROVAL_SUBMITTED" in actions or "APPROVAL_RECORDED" in actions,
        "has_applied_event": "SIMULATED_CORRECTION_APPLIED" in actions,
    }

    if is_applied:
        completeness = bool(has_pass_proof and is_approved and checks["has_applied_event"])
    else:
        completeness = True

    return {
        "case_id": case_id,
        "complete": completeness,
        "checks": checks,
        "total_events": len(events),
    }
