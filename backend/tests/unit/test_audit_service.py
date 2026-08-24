"""Unit tests for append-only audit service."""

from __future__ import annotations

from pathlib import Path

from app.audit.service import get_audit_trail, record_audit_event, verify_audit_completeness
from app.domain.enums import ActorType
from app.persistence.database import Database


def test_record_and_get_audit_trail(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    try:
        evt1 = record_audit_event(
            db=db,
            actor=ActorType.SYSTEM,
            action="RUN_STARTED",
            payload={"tenant": "test-tenant"},
            run_id="run-101",
        )
        assert evt1.event_id.startswith("evt-")
        assert evt1.actor == "SYSTEM"
        assert evt1.action == "RUN_STARTED"
        assert len(evt1.digest) == 64

        evt2 = record_audit_event(
            db=db,
            actor=ActorType.USER,
            action="APPROVAL_SUBMITTED",
            payload={"notes": "looks valid"},
            case_id="case-202",
            run_id="run-101",
        )
        assert evt2.actor == "USER"

        trail_all = get_audit_trail(db)
        assert len(trail_all) == 2
        assert trail_all[0].event_id == evt1.event_id
        assert trail_all[1].event_id == evt2.event_id

        trail_case = get_audit_trail(db, case_id="case-202")
        assert len(trail_case) == 1
        assert trail_case[0].event_id == evt2.event_id
    finally:
        db.close()


def test_verify_audit_completeness_unapplied(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    try:
        # Unapplied case is trivially complete
        res = verify_audit_completeness(db, case_id="case-none")
        assert res["complete"] is True
    finally:
        db.close()
