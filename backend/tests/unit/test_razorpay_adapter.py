"""Unit tests for Razorpay Safe Adapter and Webhook Validator (PRD Phase 6)."""

from __future__ import annotations

import csv
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
from app.importers.razorpay_client import RazorpayClient, RazorpayFetchResult
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


def test_unconfigured_sync_requires_credentials_or_manual_upload(tmp_path: Path) -> None:
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
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "both the Razorpay Test Mode Key ID and Key Secret" in detail
    assert "not persisted" in detail


def test_fetch_all_payments_paginates_without_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RazorpayClient(key_id="rzp_test_example", key_secret="secret")
    calls: list[tuple[str, int, int]] = []

    def fake_get(endpoint: str, params: dict[str, int] | None = None) -> RazorpayFetchResult:
        assert params is not None
        count = params["count"]
        skip = params["skip"]
        calls.append((endpoint, count, skip))
        remaining = max(0, 250 - skip)
        size = min(count, remaining)
        return RazorpayFetchResult(
            success=True,
            skipped=False,
            reason="OK",
            items=[{"id": f"pay_{idx}"} for idx in range(skip, skip + size)],
        )

    monkeypatch.setattr(client, "_get", fake_get)
    result = client.fetch_all_payments(max_records=1000)

    assert result.success is True
    assert len(result.items) == 250
    assert calls == [
        ("payments", 100, 0),
        ("payments", 100, 100),
        ("payments", 100, 200),
    ]


def test_settlement_reconciliation_fetches_months_and_filters_requested_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import date

    client = RazorpayClient(key_id="rzp_test_example", key_secret="secret")
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_get(endpoint: str, params: dict[str, object] | None = None) -> RazorpayFetchResult:
        assert params is not None
        calls.append((endpoint, params))
        month = str(params["month"])
        items = (
            [
                {"entity_id": "pay_before", "settled_at": 1772323200},
                {"entity_id": "pay_march", "settled_at": 1773187200},
            ]
            if month == "03"
            else [{"entity_id": "pay_april", "settled_at": 1775001600}]
        )
        return RazorpayFetchResult(True, False, "OK", items)

    monkeypatch.setattr(client, "_get", fake_get)
    result = client.fetch_settlement_reconciliation(
        period_start=date(2026, 3, 2),
        period_end=date(2026, 4, 2),
        max_records=1000,
    )

    assert result.success is True
    assert [item["entity_id"] for item in result.items] == ["pay_march", "pay_april"]
    assert [(call[1]["year"], call[1]["month"]) for call in calls] == [
        (2026, "03"),
        (2026, "04"),
    ]


def test_live_order_snapshot_is_imported_but_not_reconciled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.routes_razorpay as route_module
    from app.importers.session_staging import resolve_session_dir

    request_secret = "request-only-secret-must-not-persist"

    orders = [
        {
            "id": f"order_seed_{idx:04d}",
            "entity": "order",
            "amount": 10000,
            "amount_paid": 0,
            "amount_due": 10000,
            "currency": "INR",
            "status": "created",
            "attempts": 0,
            "created_at": 1772436000 + idx,
        }
        for idx in range(549)
    ]
    payments = [
        {
            "id": "pay_captured_0001",
            "entity": "payment",
            "order_id": "order_seed_0000",
            "amount": 10000,
            "currency": "INR",
            "status": "captured",
            "fee": 236,
            "tax": 36,
            "created_at": 1772437000,
        }
    ]

    class FakeRazorpayClient:
        BASE_URL = "https://api.razorpay.com/v1"

        def __init__(self, key_id: str | None, key_secret: str | None) -> None:
            self.key_id = key_id
            self.key_secret = key_secret

        @property
        def is_configured(self) -> bool:
            return True

        def smoke_test(self) -> dict[str, object]:
            return {"status": "PASS", "read_access_verified": True}

        def fetch_all_orders(self, max_records: int, **_: object) -> RazorpayFetchResult:
            return RazorpayFetchResult(True, False, "OK", orders[:max_records])

        def fetch_all_payments(self, max_records: int, **_: object) -> RazorpayFetchResult:
            return RazorpayFetchResult(True, False, "OK", payments[:max_records])

        def fetch_all_refunds(self, max_records: int, **_: object) -> RazorpayFetchResult:
            return RazorpayFetchResult(True, False, "OK", [])

        def fetch_all_settlements(self, max_records: int, **_: object) -> RazorpayFetchResult:
            return RazorpayFetchResult(True, False, "OK", [])

        def fetch_settlement_reconciliation(self, **_: object) -> RazorpayFetchResult:
            return RazorpayFetchResult(True, False, "OK", [])

    monkeypatch.setattr(route_module, "RazorpayClient", FakeRazorpayClient)
    settings = Settings(
        db_path=tmp_path / "razorpay-orders.sqlite3",
        import_staging_root=tmp_path / "imports",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/razorpay/sync",
            json={
                "count": 1000,
                "auto_reconcile": True,
                "session_id": "orders_only",
                "key_id": "rzp_test_request_only",
                "key_secret": request_secret,
            },
        )
        payload = response.json()
        detail = client.get(f"/api/v1/razorpay/imports/{payload['import_id']}").json()

    assert response.status_code == 200
    assert payload["empty"] is False
    assert payload["orders_count"] == 549
    assert payload["payments_count"] == 1
    assert payload["source_records_count"] == 550
    assert payload["reconciliation_eligible_count"] == 1
    assert payload["reconciled"] is False
    assert payload["gateway_ready"] is False
    assert payload["settlement_reconciliation_required"] is True
    assert payload["credential_source"] == "request_scoped"
    assert payload["credentials_persisted"] is False
    assert payload["import_status"] == "STAGED"
    assert "returned no complete settlement reconciliation" in payload["message"]
    assert detail["status"] == "STAGED"
    assert detail["counts"] == {"ORDER": 549, "PAYMENT": 1}
    assert detail["excluded"] == [
        {"entity_type": "ORDER", "reason": "ORDER_IS_NOT_A_PAYMENT", "count": 549}
    ]

    inputs = resolve_session_dir(settings, "orders_only", create=False)
    assert not (inputs / "bank_entries.csv").exists()
    assert not (inputs / "ledger_entries.csv").exists()
    persisted_bytes = settings.db_path.read_bytes() + b"".join(
        path.read_bytes() for path in inputs.rglob("*") if path.is_file()
    )
    assert request_secret.encode() not in persisted_bytes


def test_official_recon_feed_completes_gateway_without_manual_razorpay_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.routes_razorpay as route_module
    from app.importers.session_staging import resolve_session_dir

    payment = {
        "id": "pay_api_0001",
        "entity": "payment",
        "order_id": "order_api_0001",
        "amount": 10000,
        "currency": "INR",
        "status": "captured",
        "fee": 236,
        "tax": 36,
        "created_at": 1772437000,
    }
    settlement = {
        "id": "setl_api_0001",
        "entity": "settlement",
        "amount": 9764,
        "status": "processed",
        "fees": 0,
        "tax": 0,
        "utr": "UTR_API_0001",
        "created_at": 1772528400,
    }
    recon = {
        "entity_id": "pay_api_0001",
        "type": "payment",
        "debit": 0,
        "credit": 10000,
        "amount": 10000,
        "currency": "INR",
        "fee": 200,
        "tax": 36,
        "settled": True,
        "created_at": 1772437000,
        "settled_at": 1772528400,
        "settlement_id": "setl_api_0001",
        "settlement_utr": "UTR_API_0001",
        "order_id": "order_api_0001",
    }

    class CompleteFakeRazorpayClient:
        BASE_URL = "https://api.razorpay.com/v1"

        def __init__(self, key_id: str | None, key_secret: str | None) -> None:
            self.key_id = key_id
            self.key_secret = key_secret

        @property
        def is_configured(self) -> bool:
            return True

        def fetch_all_orders(self, max_records: int, **_: object) -> RazorpayFetchResult:
            return RazorpayFetchResult(True, False, "OK", [])

        def fetch_all_payments(self, max_records: int, **_: object) -> RazorpayFetchResult:
            return RazorpayFetchResult(True, False, "OK", [payment])

        def fetch_all_refunds(self, max_records: int, **_: object) -> RazorpayFetchResult:
            return RazorpayFetchResult(True, False, "OK", [])

        def fetch_all_settlements(self, max_records: int, **_: object) -> RazorpayFetchResult:
            return RazorpayFetchResult(True, False, "OK", [settlement])

        def fetch_settlement_reconciliation(self, **_: object) -> RazorpayFetchResult:
            return RazorpayFetchResult(True, False, "OK", [recon])

    monkeypatch.setattr(route_module, "RazorpayClient", CompleteFakeRazorpayClient)
    settings = Settings(
        db_path=tmp_path / "complete-api.sqlite3",
        import_staging_root=tmp_path / "imports",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/razorpay/sync",
            json={
                "session_id": "complete_api",
                "key_id": "rzp_test_request_only",
                "key_secret": "request-only-secret",
                "period_start": "2026-03-01",
                "period_end": "2026-03-31",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["gateway_ready"] is True
    assert payload["settlement_reconciliation_required"] is False
    assert payload["settlement_reconciliation_count"] == 1
    assert payload["source_revisions"]["payments"]["accepted_count"] == 1
    assert payload["source_revisions"]["settlements"]["accepted_count"] == 1

    session = resolve_session_dir(settings, "complete_api", create=False)
    with (session / "payments.csv").open(encoding="utf-8", newline="") as handle:
        payment_row = next(csv.DictReader(handle))
    with (session / "settlements.csv").open(encoding="utf-8", newline="") as handle:
        settlement_row = next(csv.DictReader(handle))
    assert payment_row["settlement_id"] == "setl_api_0001"
    assert settlement_row == {
        "settlement_id": "setl_api_0001",
        "settled_at_utc": "2026-03-03T09:00:00Z",
        "window_start_utc": "2026-03-02T07:36:40Z",
        "window_end_utc": "2026-03-02T07:36:40Z",
        "status": "PROCESSED",
        "currency": "INR",
        "gross_credit": "100.00",
        "fee_amount": "2.00",
        "tax_amount": "0.36",
        "adjustment_amount": "0.00",
        "net_amount": "97.64",
        "utr": "UTR_API_0001",
    }
