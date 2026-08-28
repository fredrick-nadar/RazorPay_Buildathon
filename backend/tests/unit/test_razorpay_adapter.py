"""Unit tests for Razorpay Safe Adapter and Webhook Validator (PRD Phase 6)."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.audit.service import get_audit_trail
from app.config import Settings
from app.importers.adapters import QuarantineSignal
from app.importers.razorpay import (
    RazorpayAdapter,
    WebhookSignatureError,
    process_razorpay_webhook_event,
    verify_razorpay_webhook_signature,
)
from app.importers.razorpay_client import RazorpayClient
from app.main import create_app
from app.persistence.database import Database


def test_normalize_payment_valid() -> None:
    payload = {
        "id": "pay_K1234567890abc",
        "entity": "payment",
        "amount": 50000,
        "currency": "INR",
        "status": "captured",
        "order_id": "order_H123456",
        "fee": 1180,
        "tax": 180,
        "created_at": 1724000000,
        "settlement_id": "setl_S123456",
    }
    record = RazorpayAdapter.normalize_payment(payload)

    assert record.payment_id == "pay_K1234567890abc"
    assert record.gross_amount_paise == 50000
    assert record.fee_paise == 1180
    assert record.tax_paise == 180
    assert record.net_paise == 50000 - 1180 - 180
    assert record.currency == "INR"
    assert record.status == "CAPTURED"
    assert record.provenance.source_record_id == "pay_K1234567890abc"


def test_normalize_payment_invalid_currency() -> None:
    payload = {
        "id": "pay_USD123",
        "amount": 50000,
        "currency": "USD",
        "status": "captured",
        "created_at": 1724000000,
    }
    with pytest.raises(QuarantineSignal) as exc:
        RazorpayAdapter.normalize_payment(payload)
    assert "UNSUPPORTED_CURRENCY" in str(exc.value)


def test_normalize_refund_valid() -> None:
    payload = {
        "id": "rfnd_R123456",
        "payment_id": "pay_K1234567890abc",
        "amount": 15000,
        "currency": "INR",
        "status": "processed",
        "created_at": 1724001000,
        "settlement_id": "setl_S123456",
    }
    record = RazorpayAdapter.normalize_refund(payload)

    assert record.refund_id == "rfnd_R123456"
    assert record.payment_id == "pay_K1234567890abc"
    assert record.refund_amount_paise == 15000
    assert record.status == "PROCESSED"


def test_normalize_settlement_valid() -> None:
    payload = {
        "id": "setl_S987654",
        "amount": 48640,
        "currency": "INR",
        "status": "processed",
        "fees": 1180,
        "tax": 180,
        "adjustment": 0,
        "settled_at": 1724010000,
        "utr": "RATN12345678901",
    }
    record = RazorpayAdapter.normalize_settlement(payload)

    assert record.settlement_id == "setl_S987654"
    assert record.net_amount_paise == 48640
    assert record.fee_paise == 1180
    assert record.tax_paise == 180
    assert record.gross_credit_paise == 48640 + 1180 + 180
    assert record.utr == "RATN12345678901"


def test_webhook_signature_verification() -> None:
    secret = "rzp_webhook_secret_xyz123"
    body = b'{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_123"}}}}'
    valid_sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    assert verify_razorpay_webhook_signature(body, valid_sig, secret) is True
    assert verify_razorpay_webhook_signature(body, "tampered_signature", secret) is False
    assert verify_razorpay_webhook_signature(body, valid_sig, "wrong_secret") is False
    assert verify_razorpay_webhook_signature(body, None, secret) is False


def test_webhook_processing_audits_invalid_signature(tmp_path: Path) -> None:
    db = Database(tmp_path / "webhook_test.sqlite3")
    secret = "secret_key_123"
    body = b'{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_123"}}}}'

    with pytest.raises(WebhookSignatureError):
        process_razorpay_webhook_event(
            raw_body=body,
            signature_header="bad_sig",
            secret=secret,
            db=db,
            run_id="run-test",
        )

    trail = get_audit_trail(db, run_id="run-test")
    assert len(trail) >= 1
    rejection_event = next(e for e in trail if e.action == "WEBHOOK_SIGNATURE_REJECTED")
    assert rejection_event.payload["reason"] == "HMAC_SHA256_MISMATCH"


def test_razorpay_client_unconfigured_skips_gracefully() -> None:
    client = RazorpayClient(key_id=None, key_secret=None)
    assert not client.is_configured

    res = client.fetch_payments()
    assert res.skipped is True
    assert res.success is False
    assert "not configured" in res.reason

    smoke = client.smoke_test()
    assert smoke["status"] == "SKIPPED"
    assert smoke["read_access_verified"] is False


def test_unconfigured_sync_uses_exact_synthetic_fallback(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "razorpay-sync.sqlite3",
        razorpay_key_id=None,
        razorpay_key_secret=None,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/razorpay/sync",
            json={"count": 25, "auto_reconcile": False},
        )
        raw_payments_path = (
            Path(__file__).resolve().parents[3] / "tmp/razorpay_live/inputs/raw_payments.json"
        )
        first_raw_payload = raw_payments_path.read_bytes()
        repeated = client.post(
            "/api/v1/razorpay/sync",
            json={"count": 25, "auto_reconcile": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["payments_count"] == 520
    assert payload["refunds_count"] == 0
    assert payload["settlements_count"] == 11
    assert payload["data_source"] == "synthetic_fallback"
    assert "synthetic dataset" in payload["provider_warning"]
    assert repeated.status_code == 200
    assert raw_payments_path.read_bytes() == first_raw_payload

    payments_csv = Path(__file__).resolve().parents[3] / "tmp/razorpay_live/inputs/payments.csv"
    first_data_row = payments_csv.read_text(encoding="utf-8").splitlines()[1]
    assert ",100.75,2.02,0.36," in first_data_row
