from __future__ import annotations

from pathlib import Path

import pytest

from app.persistence.database import Database
from app.persistence.gateway_imports import (
    DOSSIER_PAGE_LIMIT_MAX,
    GatewayEntity,
    get_demo_evidence,
    get_gateway_import,
    mark_gateway_import_staged,
    persist_gateway_snapshot,
    record_demo_evidence,
)


def test_gateway_snapshot_is_immutable_and_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "gateway.sqlite3")
    try:
        entities = [
            GatewayEntity(
                entity_type="ORDER",
                entity_id="order_1",
                payload={"id": "order_1", "status": "created", "amount": 10000},
                reconciliation_eligible=False,
                exclusion_reason="ORDER_IS_NOT_A_PAYMENT",
            )
        ]
        first = persist_gateway_snapshot(
            db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="rzp_test_example",
            entities=entities,
        )
        second = persist_gateway_snapshot(
            db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="rzp_test_example",
            entities=entities,
        )

        assert first.reused is False
        assert second.reused is True
        assert first.import_id == second.import_id
        captured = get_gateway_import(db, first.import_id)
        assert captured is not None
        assert captured["status"] == "CAPTURED"
        mark_gateway_import_staged(db, first.import_id)
        staged = get_gateway_import(db, first.import_id)
        assert staged is not None
        assert staged["status"] == "STAGED"
        assert len(db.query_all("SELECT * FROM gateway_imports")) == 1
        assert len(db.query_all("SELECT * FROM gateway_source_entities")) == 1

        changed = persist_gateway_snapshot(
            db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="rzp_test_example",
            entities=[
                GatewayEntity(
                    entity_type="ORDER",
                    entity_id="order_1",
                    payload={"id": "order_1", "status": "paid", "amount": 10000},
                    reconciliation_eligible=False,
                    exclusion_reason="ORDER_IS_NOT_A_PAYMENT",
                )
            ],
        )
        assert changed.import_id != first.import_id
        assert len(db.query_all("SELECT * FROM gateway_imports")) == 2
        assert len(db.query_all("SELECT * FROM gateway_source_entities")) == 2
    finally:
        db.close()


def _payment(index: int) -> GatewayEntity:
    return GatewayEntity(
        entity_type="PAYMENT",
        entity_id=f"pay_{index:04d}",
        payload={
            "id": f"pay_{index:04d}",
            "order_id": f"order_{index:04d}",
            "status": "captured",
            "currency": "INR",
            "amount": 10000 + index,
            "created_at": 1735689600 + index,
        },
        reconciliation_eligible=True,
        exclusion_reason=None,
        readiness_state="AWAITING_RAZORPAY_SETTLEMENT",
    )


def test_payment_dossier_reports_true_total_and_pages_without_hiding_rows(
    tmp_path: Path,
) -> None:
    """A dossier page must never imply that every imported record is present."""
    db = Database(tmp_path / "dossier.sqlite3")
    try:
        total = 130
        snapshot = persist_gateway_snapshot(
            db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="rzp_test_example",
            entities=[_payment(i) for i in range(total)],
        )

        first = get_gateway_import(db, snapshot.import_id, dossier_limit=25, dossier_offset=0)
        assert first is not None
        assert first["payment_dossier_total"] == total
        assert len(first["payment_dossier"]) == 25
        assert first["payment_dossier_limit"] == 25
        assert first["payment_dossier_offset"] == 0
        assert first["payment_dossier_truncated"] is True

        # Paging must cover every record exactly once, in a stable order.
        seen: list[str] = []
        offset = 0
        while True:
            page = get_gateway_import(
                db, snapshot.import_id, dossier_limit=50, dossier_offset=offset
            )
            assert page is not None
            assert page["payment_dossier_total"] == total
            seen.extend(item["payment_id"] for item in page["payment_dossier"])
            if not page["payment_dossier_truncated"]:
                break
            offset += 50
        assert seen == sorted(item.entity_id for item in [_payment(i) for i in range(total)])
        assert len(seen) == total

        last = get_gateway_import(db, snapshot.import_id, dossier_limit=25, dossier_offset=125)
        assert last is not None
        assert len(last["payment_dossier"]) == 5
        assert last["payment_dossier_truncated"] is False
    finally:
        db.close()


def test_payment_dossier_page_window_is_validated(tmp_path: Path) -> None:
    db = Database(tmp_path / "dossier-bounds.sqlite3")
    try:
        snapshot = persist_gateway_snapshot(
            db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="rzp_test_example",
            entities=[_payment(0)],
        )
        for bad_limit in (0, -1, DOSSIER_PAGE_LIMIT_MAX + 1):
            with pytest.raises(ValueError, match="dossier_limit"):
                get_gateway_import(db, snapshot.import_id, dossier_limit=bad_limit)
        with pytest.raises(ValueError, match="dossier_offset"):
            get_gateway_import(db, snapshot.import_id, dossier_offset=-1)
    finally:
        db.close()


def test_demo_evidence_is_readable_per_session_after_restart(tmp_path: Path) -> None:
    """Reopening a session must restore the SYNTHETIC_DEMO label from SQLite."""
    db_path = tmp_path / "demo-evidence.sqlite3"
    db = Database(db_path)
    try:
        snapshot = persist_gateway_snapshot(
            db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="rzp_test_example",
            entities=[_payment(0)],
        )
        assert (
            get_demo_evidence(db, import_id=snapshot.import_id, session_id="session_alpha") is None
        )
        evidence_id, reused = record_demo_evidence(
            db,
            import_id=snapshot.import_id,
            session_id="session_alpha",
            manifest_hash="a" * 64,
        )
        assert reused is False
    finally:
        db.close()

    # A fresh connection stands in for an API restart or a browser refresh.
    reopened = Database(db_path)
    try:
        restored = get_demo_evidence(
            reopened, import_id=snapshot.import_id, session_id="session_alpha"
        )
        assert restored is not None
        assert restored["evidence_id"] == evidence_id
        assert restored["provenance"] == "SYNTHETIC_DEMO"
        assert restored["production_eligible"] is False
        assert restored["manifest_hash"] == "a" * 64
        # Provenance never leaks across sessions.
        assert (
            get_demo_evidence(reopened, import_id=snapshot.import_id, session_id="session_beta")
            is None
        )
    finally:
        reopened.close()
