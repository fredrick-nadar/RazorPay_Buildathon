"""Deterministic, explicitly synthetic settlement evidence for Razorpay Test Mode.

This module never calls Razorpay and never writes financial truth tables.  It
derives a labelled demo bundle from immutable official Test Mode payments and
refunds so the end-to-end reconciliation workflow can be demonstrated when the
gateway sandbox does not emit settlement records.
"""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, datetime
from typing import Any

from app.domain.money import require_paise

DEMO_BATCH_SIZE = 20
DEMO_ACCOUNT_FINGERPRINT = "SYNTHETIC-DEMO-BANK"


class DemoEvidenceError(ValueError):
    """Official Test Mode rows cannot safely produce a demo evidence bundle."""


def _money(value: Any, field: str) -> int:
    try:
        return int(require_paise(value))
    except (TypeError, ValueError) as exc:
        raise DemoEvidenceError(f"Invalid integer-paise field: {field}") from exc


def _decimal(paise: int) -> str:
    checked = int(require_paise(paise))
    sign = "-" if checked < 0 else ""
    absolute = abs(checked)
    return f"{sign}{absolute // 100}.{absolute % 100:02d}"


def _timestamp(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).date().isoformat()


def _stable_id(prefix: str, import_id: str, index: int) -> str:
    digest = hashlib.sha256(f"{import_id}|{prefix}|{index}".encode()).hexdigest()[:16]
    return f"demo_{prefix}_{digest}"


def _csv(headers: tuple[str, ...], rows: list[list[str]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue()


def build_demo_evidence(
    *,
    import_id: str,
    payments: list[dict[str, Any]],
    refunds: list[dict[str, Any]],
    include_merchant_sources: bool = False,
) -> dict[str, Any]:
    """Build gateway evidence only by default.

    The explicit full-bundle option is for offline/legacy fixture reproduction,
    never the import endpoint. Bank receipt and accounting require separate
    merchant uploads; a gateway-derived file cannot establish either fact.
    """
    captured = sorted(
        (item for item in payments if item.get("status") == "captured"),
        key=lambda item: (item.get("created_at", 0), str(item.get("id") or "")),
    )
    if not captured:
        raise DemoEvidenceError("No captured Test Mode payments are available")

    payment_ids = {str(item.get("id") or "") for item in captured}
    if "" in payment_ids or len(payment_ids) != len(captured):
        raise DemoEvidenceError("Captured payments contain missing or duplicate IDs")
    processed_refunds = sorted(
        (
            item
            for item in refunds
            if item.get("status") == "processed"
            and str(item.get("payment_id") or "") in payment_ids
        ),
        key=lambda item: (item.get("created_at", 0), str(item.get("id") or "")),
    )
    refunds_by_payment: dict[str, list[dict[str, Any]]] = {}
    for refund in processed_refunds:
        refunds_by_payment.setdefault(str(refund["payment_id"]), []).append(refund)

    payment_rows: list[list[str]] = []
    refund_rows: list[list[str]] = []
    settlement_rows: list[list[str]] = []
    bank_rows: list[list[str]] = []
    ledger_rows: list[list[str]] = []
    settlement_count = (len(captured) + DEMO_BATCH_SIZE - 1) // DEMO_BATCH_SIZE

    for batch_index in range(settlement_count):
        members = captured[batch_index * DEMO_BATCH_SIZE : (batch_index + 1) * DEMO_BATCH_SIZE]
        settlement_id = _stable_id("setl", import_id, batch_index)
        utr = f"DEMO{hashlib.sha256(settlement_id.encode()).hexdigest()[:18].upper()}"
        created_values = [_money(item.get("created_at"), "payment.created_at") for item in members]
        settled_at = max(created_values) + 86_400
        gross_total = 0
        fee_total = 0
        tax_total = 0
        refund_total = 0

        for payment in members:
            payment_id = str(payment["id"])
            gross = _money(payment.get("amount"), "payment.amount")
            fee_including_tax = _money(payment.get("fee"), "payment.fee")
            tax = _money(payment.get("tax"), "payment.tax")
            fee = fee_including_tax - tax
            if fee < 0:
                raise DemoEvidenceError(f"Payment {payment_id} has tax greater than fee")
            currency = str(payment.get("currency") or "")
            if currency != "INR":
                raise DemoEvidenceError("Demo settlement evidence supports INR only")
            created_at = _money(payment.get("created_at"), "payment.created_at")
            gross_total += gross
            fee_total += fee
            tax_total += tax
            payment_rows.append(
                [
                    payment_id,
                    str(payment.get("order_id") or ""),
                    "CAPTURED",
                    currency,
                    _decimal(gross),
                    _decimal(fee),
                    _decimal(tax),
                    _timestamp(created_at),
                    settlement_id,
                ]
            )
            if include_merchant_sources:
                ledger_rows.append(
                    [
                        _stable_id("ledpay", import_id, len(ledger_rows)),
                        "2100-MERCHANT-SETTLEMENT",
                        _date(created_at),
                        currency,
                        _decimal(gross - fee - tax),
                        payment_id,
                        "PAYMENT",
                        f"SYNTHETIC_DEMO payment clearing {payment_id}",
                        "IMPORTED",
                    ]
                )
            for refund in refunds_by_payment.get(payment_id, []):
                refund_amount = _money(refund.get("amount"), "refund.amount")
                refund_created = _money(refund.get("created_at"), "refund.created_at")
                refund_id = str(refund.get("id") or "")
                if not refund_id:
                    raise DemoEvidenceError("Processed refund is missing its ID")
                refund_total += refund_amount
                refund_rows.append(
                    [
                        refund_id,
                        payment_id,
                        "PROCESSED",
                        currency,
                        _decimal(refund_amount),
                        _timestamp(refund_created),
                        settlement_id,
                    ]
                )
                if include_merchant_sources:
                    ledger_rows.append(
                        [
                            _stable_id("ledref", import_id, len(ledger_rows)),
                            "2100-MERCHANT-SETTLEMENT",
                            _date(refund_created),
                            currency,
                            _decimal(-refund_amount),
                            refund_id,
                            "REFUND",
                            f"SYNTHETIC_DEMO refund clearing {refund_id}",
                            "IMPORTED",
                        ]
                    )

        net = gross_total - fee_total - tax_total - refund_total
        if net <= 0:
            raise DemoEvidenceError(f"Settlement batch {batch_index + 1} has non-positive net")
        settlement_rows.append(
            [
                settlement_id,
                _timestamp(settled_at),
                _timestamp(min(created_values)),
                _timestamp(max(created_values)),
                "PROCESSED",
                "INR",
                _decimal(gross_total),
                _decimal(fee_total),
                _decimal(tax_total),
                _decimal(-refund_total),
                _decimal(net),
                utr,
            ]
        )
        if include_merchant_sources:
            bank_rows.append(
                [
                    _stable_id("bank", import_id, batch_index),
                    _timestamp(settled_at + 300),
                    _date(settled_at),
                    "INR",
                    _decimal(net),
                    f"SYNTHETIC_DEMO NEFT CR {utr} {settlement_id}",
                    utr,
                    DEMO_ACCOUNT_FINGERPRINT,
                ]
            )
            ledger_rows.append(
                [
                    _stable_id("ledsetl", import_id, batch_index),
                    "1100-BANK",
                    _date(settled_at),
                    "INR",
                    _decimal(net),
                    settlement_id,
                    "SETTLEMENT",
                    f"SYNTHETIC_DEMO settlement credit {settlement_id}",
                    "IMPORTED",
                ]
            )

    files = {
        "payments.csv": _csv(
            (
                "payment_id",
                "order_id",
                "status",
                "currency",
                "gross_amount",
                "fee_amount",
                "tax_amount",
                "captured_at_utc",
                "settlement_id",
            ),
            payment_rows,
        ),
        "refunds.csv": _csv(
            (
                "refund_id",
                "payment_id",
                "status",
                "currency",
                "refund_amount",
                "created_at_utc",
                "settlement_id",
            ),
            refund_rows,
        ),
        "settlements.csv": _csv(
            (
                "settlement_id",
                "settled_at_utc",
                "window_start_utc",
                "window_end_utc",
                "status",
                "currency",
                "gross_credit",
                "fee_amount",
                "tax_amount",
                "adjustment_amount",
                "net_amount",
                "utr",
            ),
            settlement_rows,
        ),
    }
    if include_merchant_sources:
        files["bank_entries.csv"] = _csv(
            (
                "bank_entry_id",
                "posted_at_utc",
                "value_date",
                "currency",
                "signed_amount",
                "narration",
                "utr",
                "account_fingerprint",
            ),
            bank_rows,
        )
        files["ledger_entries.csv"] = _csv(
            (
                "ledger_entry_id",
                "account_code",
                "accounting_date",
                "currency",
                "signed_amount",
                "source_reference",
                "source_type",
                "description",
                "entry_origin",
            ),
            ledger_rows,
        )
    manifest_hash = hashlib.sha256(
        "".join(
            f"{name}:{hashlib.sha256(value.encode()).hexdigest()}"
            for name, value in sorted(files.items())
        ).encode()
    ).hexdigest()
    return {
        "files": files,
        "scope": "FULL_DEMO" if include_merchant_sources else "GATEWAY_ONLY",
        "manifest_hash": manifest_hash,
        "payments_count": len(payment_rows),
        "refunds_count": len(refund_rows),
        "settlements_count": len(settlement_rows),
        "bank_entries_count": len(bank_rows),
        "ledger_entries_count": len(ledger_rows),
    }


DEMO_ORIGIN = "SYNTHETIC_DEMO"
GATEWAY_DEMO_SOURCES: tuple[str, ...] = ("payments", "refunds", "settlements")
DEMO_BUNDLE_SOURCES: tuple[str, ...] = (
    "payments",
    "refunds",
    "settlements",
    "bank_entries",
    "ledger_entries",
)


def derive_demo_activation(
    active_sources: dict[str, Any],
    import_id: str,
    manifest_hash: str,
    *,
    expected_sources: tuple[str, ...] = DEMO_BUNDLE_SOURCES,
) -> dict[str, Any]:
    """Decide whether a generated demo bundle is STILL the active evidence.

    A persisted demo record proves a bundle was GENERATED once. It does not
    prove the bundle is still active: an API re-import or a manual CSV can
    supersede any of its scoped sources afterwards. Activation is therefore
    derived from the authoritative current session manifest, never from the
    existence of the database row.

    A source counts as demo-active only when the currently active revision for
    that source type carries both the SYNTHETIC_DEMO origin and this import's
    id and verified provenance binding. The caller must verify the immutable
    raw/canonical bytes first; a full bundle must also match the persisted
    aggregate hash. Missing integrity evidence fails closed as UNKNOWN.
    """
    active: list[str] = []
    unknown = False
    superseded: list[str] = []
    for source_type in expected_sources:
        current = active_sources.get(source_type)
        if (
            isinstance(current, dict)
            and str(current.get("origin") or "") == DEMO_ORIGIN
            and str(current.get("external_import_id") or "") == import_id
        ):
            metadata = current.get("demo_metadata", {})
            if (
                metadata.get("manifest_hash") == manifest_hash
                and metadata.get("derived_from_gateway_import") == import_id
                and metadata.get("canonical_filename") == f"{source_type}.csv"
                and metadata.get("provenance") == DEMO_ORIGIN
            ):
                active.append(source_type)
            else:
                unknown = True
        else:
            superseded.append(source_type)
    if len(active) == len(expected_sources):
        actual_hash = hashlib.sha256(
            "".join(
                f"{source}.csv:{active_sources[source]['canonical_sha256']}"
                for source in sorted(expected_sources)
            ).encode()
        ).hexdigest()
        unknown = actual_hash != manifest_hash
    if unknown:
        state = "UNKNOWN"
        active = []
    elif not active:
        state = "SUPERSEDED"
    elif superseded:
        state = "PARTIALLY_ACTIVE"
    else:
        state = "ACTIVE"
    return {
        "activation_state": state,
        "active_demo_sources": active,
        "superseded_sources": superseded,
        "expected_sources": list(expected_sources),
    }
