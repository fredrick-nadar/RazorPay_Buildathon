"""Replay filesystem activation receipts into SQLite in an idempotent transaction."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.audit.service import record_audit_event
from app.domain.enums import ActorType
from app.persistence.database import Database
from app.persistence.gateway_imports import mark_gateway_import_staged, record_demo_evidence


def project_activation(db: Database, receipt_id: str, receipt: dict[str, Any]) -> None:
    material = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    if receipt_id != "act-" + hashlib.sha256(material.encode()).hexdigest():
        raise ValueError("Activation receipt failed its integrity check.")
    with db.transaction(immediate=True):
        # The append-only audit event is also the durable delivery acknowledgement.
        if db.query_one("SELECT event_id FROM audit_log WHERE event_id = ?", (receipt_id,)):
            return
        payload = dict(receipt)
        action = payload.pop("action")
        if action == "SYNTHETIC_DEMO_EVIDENCE_STAGED":
            if payload["scope"] != "GATEWAY_ONLY":
                raise ValueError("Only gateway-only demo activation is permitted.")
            evidence_id, reused = record_demo_evidence(
                db,
                import_id=payload["import_id"],
                session_id=payload["session_id"],
                manifest_hash=payload["manifest_hash"],
                scope=payload["scope"],
            )
            payload.update(evidence_id=evidence_id, reused=reused)
        elif action == "GATEWAY_SNAPSHOT_STAGED":
            mark_gateway_import_staged(db, payload["import_id"])
        else:
            raise ValueError("Unknown activation receipt action.")
        record_audit_event(
            db,
            actor=ActorType.SYSTEM,
            action=action,
            payload=payload,
            event_id=receipt_id,
        )
