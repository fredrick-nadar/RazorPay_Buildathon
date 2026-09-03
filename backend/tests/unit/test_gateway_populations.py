"""Entity-scoped counts for the gateway intake dossier (REVIEW-004).

The all-entity ``readiness_state`` roll-up cannot answer questions about
payments, because the same states are assigned to refunds too. These tests pin
each number to exactly one population so no surface can mix them again.
"""

from __future__ import annotations

from pathlib import Path

from app.persistence.database import Database
from app.persistence.gateway_imports import (
    GatewayEntity,
    get_gateway_import,
    persist_gateway_snapshot,
)


def _payment(
    entity_id: str,
    status: str,
    *,
    eligible: bool,
    readiness: str,
    reason: str | None = None,
) -> GatewayEntity:
    return GatewayEntity(
        entity_type="PAYMENT",
        entity_id=entity_id,
        payload={
            "id": entity_id,
            "order_id": f"order_{entity_id}",
            "status": status,
            "currency": "INR",
            "amount": 10000,
            "created_at": 1772437000,
            "fee": 236,
            "tax": 36,
        },
        reconciliation_eligible=eligible,
        exclusion_reason=reason,
        readiness_state=readiness,
    )


def _refund(
    entity_id: str, status: str, *, eligible: bool, readiness: str, reason: str | None = None
) -> GatewayEntity:
    return GatewayEntity(
        entity_type="REFUND",
        entity_id=entity_id,
        payload={
            "id": entity_id,
            "payment_id": "pay_captured",
            "status": status,
            "currency": "INR",
            "amount": 1000,
            "created_at": 1772439000,
        },
        reconciliation_eligible=eligible,
        exclusion_reason=reason,
        readiness_state=readiness,
    )


def _snapshot(db: Database, entities: list[GatewayEntity]) -> str:
    return persist_gateway_snapshot(
        db,
        provider="RAZORPAY",
        mode="TEST",
        credential_identifier="rzp_test_populations",
        entities=entities,
    ).import_id


def test_payment_counts_exclude_refunds_from_the_pending_figure(tmp_path: Path) -> None:
    """A pending refund must never inflate the pending-payment count."""
    db = Database(tmp_path / "mixed.sqlite3")
    try:
        import_id = _snapshot(
            db,
            [
                _payment(
                    "pay_captured",
                    "captured",
                    eligible=True,
                    readiness="AWAITING_RAZORPAY_SETTLEMENT",
                ),
                _payment(
                    "pay_failed",
                    "failed",
                    eligible=False,
                    readiness="NOT_RECONCILIATION_ELIGIBLE",
                    reason="PAYMENT_NOT_CAPTURED",
                ),
                _refund(
                    "rfnd_ok",
                    "processed",
                    eligible=True,
                    readiness="AWAITING_RAZORPAY_SETTLEMENT",
                ),
            ],
        )
        result = get_gateway_import(db, import_id)
        assert result is not None

        # The all-entity roll-up counts the refund alongside the payment. It is
        # kept for compatibility but must not be read as a payment figure.
        assert result["readiness_counts"]["AWAITING_RAZORPAY_SETTLEMENT"] == 2

        payments = result["payment_counts"]
        assert payments["total"] == 2
        assert payments["captured"] == 1
        assert payments["eligible"] == 1
        # The refund is NOT here. This is the number the dossier line reports.
        assert payments["awaiting_settlement"] == 1
        assert payments["not_eligible"] == 1
        assert payments["settlement_available"] == 0

        refunds = result["refund_counts"]
        assert refunds["total"] == 1
        assert refunds["processed"] == 1
        assert refunds["eligible"] == 1
        assert refunds["awaiting_settlement"] == 1

        # The dossier is every payment record, failed ones included, and each
        # row carries the status that makes that visible.
        assert result["payment_dossier_total"] == 2
        by_id = {row["payment_id"]: row for row in result["payment_dossier"]}
        assert by_id["pay_captured"]["status"] == "captured"
        assert by_id["pay_failed"]["status"] == "failed"
        assert by_id["pay_failed"]["readiness_state"] == "NOT_RECONCILIATION_ELIGIBLE"
    finally:
        db.close()


def test_captured_is_counted_separately_from_eligible(tmp_path: Path) -> None:
    """Captured and eligible are different populations and must not be merged.

    A captured payment missing the reconciliation fields is ineligible, yet the
    demo generator still accepts it because it selects on provider status. The
    two counts therefore have to be reported independently.
    """
    db = Database(tmp_path / "captured.sqlite3")
    try:
        incomplete = GatewayEntity(
            entity_type="PAYMENT",
            entity_id="pay_no_fee",
            payload={
                "id": "pay_no_fee",
                "status": "captured",
                "currency": "INR",
                "amount": 10000,
                "created_at": 1772437000,
            },
            reconciliation_eligible=False,
            exclusion_reason="PAYMENT_MISSING_RECONCILIATION_FIELDS",
            readiness_state="NOT_RECONCILIATION_ELIGIBLE",
        )
        import_id = _snapshot(
            db,
            [
                _payment(
                    "pay_ok", "captured", eligible=True, readiness="AWAITING_RAZORPAY_SETTLEMENT"
                ),
                incomplete,
            ],
        )
        counts = get_gateway_import(db, import_id)
        assert counts is not None
        payments = counts["payment_counts"]
        assert payments["total"] == 2
        # Both are captured at the provider...
        assert payments["captured"] == 2
        # ...but only one can be reconciled.
        assert payments["eligible"] == 1
        assert payments["not_eligible"] == 1
    finally:
        db.close()


def test_settled_payments_are_reported_apart_from_pending(tmp_path: Path) -> None:
    db = Database(tmp_path / "settled.sqlite3")
    try:
        import_id = _snapshot(
            db,
            [
                _payment(
                    "pay_settled", "captured", eligible=True, readiness="SETTLEMENT_AVAILABLE"
                ),
                _payment(
                    "pay_pending",
                    "captured",
                    eligible=True,
                    readiness="AWAITING_RAZORPAY_SETTLEMENT",
                ),
            ],
        )
        result = get_gateway_import(db, import_id)
        assert result is not None
        payments = result["payment_counts"]
        assert payments["settlement_available"] == 1
        assert payments["awaiting_settlement"] == 1
        assert payments["eligible"] == 2
    finally:
        db.close()


def test_counts_are_zero_for_an_import_without_those_entities(tmp_path: Path) -> None:
    """Absent is zero, not null, and never an error."""
    db = Database(tmp_path / "orders-only.sqlite3")
    try:
        import_id = _snapshot(
            db,
            [
                GatewayEntity(
                    entity_type="ORDER",
                    entity_id="order_only",
                    payload={"id": "order_only", "status": "paid", "amount": 10000},
                    reconciliation_eligible=False,
                    exclusion_reason="ORDER_IS_NOT_A_PAYMENT",
                )
            ],
        )
        result = get_gateway_import(db, import_id)
        assert result is not None
        for scoped in (result["payment_counts"], result["refund_counts"]):
            assert scoped["total"] == 0
            assert scoped["eligible"] == 0
            assert scoped["awaiting_settlement"] == 0
            assert scoped["not_eligible"] == 0
        assert result["payment_counts"]["captured"] == 0
        assert result["refund_counts"]["processed"] == 0
        assert result["payment_dossier"] == []
        assert result["payment_dossier_total"] == 0
        assert result["payment_dossier_truncated"] is False
    finally:
        db.close()
