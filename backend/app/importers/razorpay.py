"""Razorpay Safe Adapter and Webhook Signature Validator (PRD Phase 6).

Normalizes Razorpay JSON objects and webhook payloads into typed ARGUS
domain records. Enforces cryptographic HMAC-SHA256 signature verification,
strict INR paise arithmetic, immutable source provenance, and audit logging
for tampered or invalid payloads.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

from app.audit.service import record_audit_event
from app.domain.enums import ActorType, Currency, QuarantineReason
from app.domain.money import require_paise
from app.domain.records import (
    PaymentRecord,
    Provenance,
    RefundRecord,
    SettlementRecord,
)
from app.importers.adapters import QuarantineSignal
from app.persistence.database import Database


class WebhookSignatureError(Exception):
    """Raised when an incoming webhook fails HMAC signature verification."""

    def __init__(self, message: str, reason: str = "INVALID_SIGNATURE") -> None:
        super().__init__(message)
        self.reason = reason


def verify_razorpay_webhook_signature(
    raw_payload: bytes | str,
    signature_header: str | None,
    secret: str,
) -> bool:
    """Verify Razorpay webhook HMAC-SHA256 signature against the raw request body.

    Uses constant-time comparison to protect against timing side-channel attacks.
    """
    if not signature_header or not secret:
        return False

    payload_bytes = raw_payload.encode("utf-8") if isinstance(raw_payload, str) else raw_payload
    expected_mac = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_mac.strip(), signature_header.strip())


class RazorpayAdapter:
    """Offline-first normalizer for Razorpay payment, refund, and settlement JSON payloads."""

    @staticmethod
    def _compute_json_provenance(
        source_file: str,
        source_row: int,
        record_id: str,
        payload: dict[str, Any],
    ) -> Provenance:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        chash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return Provenance(
            source_file=source_file,
            source_row_number=source_row,
            source_record_id=record_id,
            content_hash=chash,
        )

    @classmethod
    def normalize_payment(
        cls,
        payload: dict[str, Any],
        source_file: str = "razorpay_api_payments",
        source_row: int = 1,
    ) -> PaymentRecord:
        """Normalize a Razorpay Payment entity."""
        payment_id = payload.get("id")
        if not payment_id or not isinstance(payment_id, str):
            raise QuarantineSignal(QuarantineReason.MISSING_REQUIRED_FIELD, "Missing payment id")

        currency = payload.get("currency", "INR")
        if currency != Currency.INR.value:
            raise QuarantineSignal(
                QuarantineReason.UNSUPPORTED_CURRENCY, f"Unsupported currency {currency}"
            )

        status = payload.get("status")
        if status != "captured":
            raise QuarantineSignal(
                QuarantineReason.UNKNOWN_STATUS, f"Payment status must be captured, got {status}"
            )

        raw_amount = payload.get("amount")
        if not isinstance(raw_amount, int) or raw_amount <= 0:
            raise QuarantineSignal(
                QuarantineReason.INVALID_MONEY, f"Invalid payment amount {raw_amount}"
            )
        gross_paise = require_paise(raw_amount)

        raw_fee = payload.get("fee", 0)
        raw_tax = payload.get("tax", 0)
        fee_paise = require_paise(raw_fee if isinstance(raw_fee, int) else 0)
        tax_paise = require_paise(raw_tax if isinstance(raw_tax, int) else 0)

        created_at_epoch = payload.get("created_at")
        if not isinstance(created_at_epoch, (int, float)):
            raise QuarantineSignal(
                QuarantineReason.INVALID_TIMESTAMP, f"Invalid created_at {created_at_epoch}"
            )
        captured_at_utc = datetime.fromtimestamp(created_at_epoch, tz=UTC)

        order_id = payload.get("order_id")
        settlement_id = payload.get("settlement_id")

        provenance = cls._compute_json_provenance(source_file, source_row, payment_id, payload)

        return PaymentRecord(
            provenance=provenance,
            payment_id=payment_id,
            order_id=str(order_id) if order_id else None,
            status="CAPTURED",
            currency="INR",
            gross_amount_paise=gross_paise,
            fee_paise=fee_paise,
            tax_paise=tax_paise,
            captured_at_utc=captured_at_utc,
            settlement_id=str(settlement_id) if settlement_id else None,
        )

    @classmethod
    def normalize_refund(
        cls,
        payload: dict[str, Any],
        source_file: str = "razorpay_api_refunds",
        source_row: int = 1,
    ) -> RefundRecord:
        """Normalize a Razorpay Refund entity."""
        refund_id = payload.get("id")
        if not refund_id or not isinstance(refund_id, str):
            raise QuarantineSignal(QuarantineReason.MISSING_REQUIRED_FIELD, "Missing refund id")

        payment_id = payload.get("payment_id")
        if not payment_id or not isinstance(payment_id, str):
            raise QuarantineSignal(
                QuarantineReason.MISSING_REQUIRED_FIELD, "Missing payment_id on refund"
            )

        currency = payload.get("currency", "INR")
        if currency != Currency.INR.value:
            raise QuarantineSignal(
                QuarantineReason.UNSUPPORTED_CURRENCY, f"Unsupported currency {currency}"
            )

        status = payload.get("status")
        if status != "processed":
            raise QuarantineSignal(
                QuarantineReason.UNKNOWN_STATUS, f"Refund status must be processed, got {status}"
            )

        raw_amount = payload.get("amount")
        if not isinstance(raw_amount, int) or raw_amount <= 0:
            raise QuarantineSignal(
                QuarantineReason.INVALID_MONEY, f"Invalid refund amount {raw_amount}"
            )
        refund_paise = require_paise(raw_amount)

        created_at_epoch = payload.get("created_at")
        if not isinstance(created_at_epoch, (int, float)):
            raise QuarantineSignal(
                QuarantineReason.INVALID_TIMESTAMP, f"Invalid created_at {created_at_epoch}"
            )
        created_at_utc = datetime.fromtimestamp(created_at_epoch, tz=UTC)

        settlement_id = payload.get("settlement_id")
        provenance = cls._compute_json_provenance(source_file, source_row, refund_id, payload)

        return RefundRecord(
            provenance=provenance,
            refund_id=refund_id,
            payment_id=payment_id,
            status="PROCESSED",
            currency="INR",
            refund_amount_paise=refund_paise,
            created_at_utc=created_at_utc,
            settlement_id=str(settlement_id) if settlement_id else None,
        )

    @classmethod
    def normalize_settlement(
        cls,
        payload: dict[str, Any],
        source_file: str = "razorpay_api_settlements",
        source_row: int = 1,
    ) -> SettlementRecord:
        """Normalize a Razorpay Settlement entity."""
        settlement_id = payload.get("id")
        if not settlement_id or not isinstance(settlement_id, str):
            raise QuarantineSignal(QuarantineReason.MISSING_REQUIRED_FIELD, "Missing settlement id")

        currency = payload.get("currency", "INR")
        if currency != Currency.INR.value:
            raise QuarantineSignal(
                QuarantineReason.UNSUPPORTED_CURRENCY, f"Unsupported currency {currency}"
            )

        status = payload.get("status", "processed")
        if status != "processed":
            raise QuarantineSignal(
                QuarantineReason.UNKNOWN_STATUS,
                f"Settlement status must be processed, got {status}",
            )

        raw_net = payload.get("amount")
        if not isinstance(raw_net, int):
            raise QuarantineSignal(
                QuarantineReason.INVALID_MONEY, f"Invalid settlement net amount {raw_net}"
            )
        net_paise = require_paise(raw_net)

        raw_fee = payload.get("fees", payload.get("fee_amount", 0))
        raw_tax = payload.get("tax", payload.get("tax_amount", 0))
        raw_adj = payload.get("adjustment", payload.get("adjustment_amount", 0))

        fee_paise = require_paise(raw_fee if isinstance(raw_fee, int) else 0)
        tax_paise = require_paise(raw_tax if isinstance(raw_tax, int) else 0)
        adj_paise = require_paise(raw_adj if isinstance(raw_adj, int) else 0)

        raw_gross = payload.get(
            "gross_credit", int(net_paise) + int(fee_paise) + int(tax_paise) - int(adj_paise)
        )
        gross_credit_paise = require_paise(raw_gross)

        settled_epoch = payload.get("settled_at", payload.get("created_at"))
        if not isinstance(settled_epoch, (int, float)):
            raise QuarantineSignal(
                QuarantineReason.INVALID_TIMESTAMP, f"Invalid settlement timestamp {settled_epoch}"
            )
        settled_at_utc = datetime.fromtimestamp(settled_epoch, tz=UTC)

        window_start_epoch = payload.get("window_start_utc", settled_epoch - 86400)
        window_end_epoch = payload.get("window_end_utc", settled_epoch)
        window_start_utc = (
            datetime.fromtimestamp(window_start_epoch, tz=UTC)
            if isinstance(window_start_epoch, (int, float))
            else settled_at_utc
        )
        window_end_utc = (
            datetime.fromtimestamp(window_end_epoch, tz=UTC)
            if isinstance(window_end_epoch, (int, float))
            else settled_at_utc
        )

        utr = payload.get("utr")
        provenance = cls._compute_json_provenance(source_file, source_row, settlement_id, payload)

        return SettlementRecord(
            provenance=provenance,
            settlement_id=settlement_id,
            settled_at_utc=settled_at_utc,
            window_start_utc=window_start_utc,
            window_end_utc=window_end_utc,
            status="PROCESSED",
            currency="INR",
            gross_credit_paise=gross_credit_paise,
            fee_paise=fee_paise,
            tax_paise=tax_paise,
            adjustment_paise=adj_paise,
            net_amount_paise=net_paise,
            utr=str(utr) if utr else None,
        )


def process_razorpay_webhook_event(
    raw_body: bytes,
    signature_header: str | None,
    secret: str | None,
    db: Database | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Validate incoming Razorpay webhook, check signature, and audit any rejection."""
    if secret:
        is_valid = verify_razorpay_webhook_signature(raw_body, signature_header, secret)
        if not is_valid:
            if db is not None:
                record_audit_event(
                    db=db,
                    actor=ActorType.SYSTEM,
                    action="WEBHOOK_SIGNATURE_REJECTED",
                    payload={
                        "reason": "HMAC_SHA256_MISMATCH",
                        "has_signature_header": bool(signature_header),
                        "payload_bytes_len": len(raw_body),
                    },
                    run_id=run_id,
                )
            raise WebhookSignatureError("Invalid Razorpay webhook signature header")

    try:
        data = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        if db is not None:
            record_audit_event(
                db=db,
                actor=ActorType.SYSTEM,
                action="WEBHOOK_PAYLOAD_INVALID_JSON",
                payload={"error": str(exc)},
                run_id=run_id,
            )
        raise QuarantineSignal(
            QuarantineReason.INVALID_ROW_SHAPE, "Malformed JSON webhook body"
        ) from exc

    if db is not None:
        event_name = data.get("event", "unknown")
        record_audit_event(
            db=db,
            actor=ActorType.SYSTEM,
            action="WEBHOOK_EVENT_RECEIVED",
            payload={"event": event_name, "account_id": data.get("account_id")},
            run_id=run_id,
        )

    return dict(data)
