from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from scripts.seed_razorpay_test_lifecycle import (
    CheckoutCoordinator,
    _verify_checkout_signature,
    prepare_state,
)

from app.importers.razorpay_client import RazorpayFetchResult


class FakeClient:
    key_id = "rzp_test_public"
    key_secret = "fixture"
    is_configured = True

    def fetch_all_orders(self, max_records: int) -> RazorpayFetchResult:
        assert max_records == 1000
        return RazorpayFetchResult(
            success=True,
            skipped=False,
            reason="OK",
            items=[
                {
                    "id": "order_seed_1",
                    "status": "created",
                    "amount": 12500,
                    "currency": "INR",
                    "receipt": "argus_test_rec_0001",
                    "created_at": 1770000000,
                    "notes": {"source": "synthetic_live_seed"},
                }
            ],
        )


class FakeApi:
    def __init__(self) -> None:
        self.client = FakeClient()
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        assert extra_headers is None
        self.calls.append((method, endpoint, payload))
        status = "authorized" if method == "GET" else "captured"
        return {
            "id": "pay_seed_1",
            "order_id": "order_seed_1",
            "amount": 12500,
            "currency": "INR",
            "status": status,
            "created_at": 1770000100,
        }


def test_checkout_signature_verification_rejects_tampering() -> None:
    signature = hmac.new(
        b"fixture",
        b"order_seed_1|pay_seed_1",
        hashlib.sha256,
    ).hexdigest()
    assert _verify_checkout_signature(
        order_id="order_seed_1",
        payment_id="pay_seed_1",
        signature=signature,
        key_secret="fixture",
    )
    assert not _verify_checkout_signature(
        order_id="order_seed_1",
        payment_id="pay_tampered",
        signature=signature,
        key_secret="fixture",
    )


def test_checkout_coordinator_captures_and_persists_no_credentials(tmp_path: Path) -> None:
    api = FakeApi()
    state_path = tmp_path / "seed-state.json"
    prepare_state(api, state_path, 1)  # type: ignore[arg-type]
    coordinator = CheckoutCoordinator(api, state_path, 1)  # type: ignore[arg-type]
    signature = hmac.new(
        b"fixture",
        b"order_seed_1|pay_seed_1",
        hashlib.sha256,
    ).hexdigest()

    result = coordinator.complete(
        {
            "razorpay_order_id": "order_seed_1",
            "razorpay_payment_id": "pay_seed_1",
            "razorpay_signature": signature,
        }
    )

    assert result["payment"]["status"] == "captured"
    assert [call[:2] for call in api.calls] == [
        ("GET", "payments/pay_seed_1"),
        ("POST", "payments/pay_seed_1/capture"),
    ]
    persisted = state_path.read_bytes()
    assert b"fixture" not in persisted
    assert json.loads(persisted)["payments"][0]["payment_id"] == "pay_seed_1"


def test_checkout_coordinator_recovers_paid_order_after_lost_callback(
    tmp_path: Path,
) -> None:
    class RecoveryApi(FakeApi):
        def request(
            self,
            method: str,
            endpoint: str,
            payload: dict[str, Any] | None = None,
            extra_headers: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            assert method == "GET"
            assert payload is None
            assert extra_headers is None
            self.calls.append((method, endpoint, payload))
            if endpoint == "orders/order_seed_1":
                return {"id": "order_seed_1", "status": "paid"}
            assert endpoint == "orders/order_seed_1/payments"
            return {
                "items": [
                    {
                        "id": "pay_seed_1",
                        "order_id": "order_seed_1",
                        "amount": 12500,
                        "currency": "INR",
                        "status": "captured",
                        "created_at": 1770000100,
                    }
                ]
            }

    api = RecoveryApi()
    state_path = tmp_path / "seed-state.json"
    coordinator = CheckoutCoordinator(api, state_path, 1)  # type: ignore[arg-type]

    assert coordinator.reconcile_paid_orders() == {"recovered": 1, "completed": 1}
    assert coordinator.reconcile_paid_orders() == {"recovered": 0, "completed": 1}
    assert json.loads(state_path.read_text(encoding="utf-8"))["payments"] == [
        {
            "amount": 12500,
            "created_at": 1770000100,
            "currency": "INR",
            "order_id": "order_seed_1",
            "payment_id": "pay_seed_1",
            "status": "captured",
        }
    ]
