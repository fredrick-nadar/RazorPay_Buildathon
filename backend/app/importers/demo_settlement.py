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
DEMO_SETTLEMENT_DELAY_SECONDS = 86_400
DEMO_BANK_POSTING_DELAY_SECONDS = 300


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


def _epoch(value: Any, field: str) -> int:
    try:
        epoch = _money(value, field)
    except DemoEvidenceError as exc:
        raise DemoEvidenceError(f"Invalid UTC epoch field: {field}") from exc
    if epoch < 0:
        raise DemoEvidenceError(f"Invalid UTC epoch field: {field}")
    try:
        datetime.fromtimestamp(epoch, UTC)
    except (OSError, OverflowError, ValueError) as exc:
        raise DemoEvidenceError(f"Invalid UTC epoch field: {field}") from exc
    return epoch


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
    captured: list[dict[str, Any]] = []
    seen_payment_ids: set[str] = set()
    for item in payments:
        if item.get("status") != "captured":
            continue
        payment_id = str(item.get("id") or "")
        if not payment_id or payment_id in seen_payment_ids:
            raise DemoEvidenceError("Captured payments contain missing or duplicate IDs")
        seen_payment_ids.add(payment_id)
        gross = _money(item.get("amount"), "payment.amount")
        fee_including_tax = _money(item.get("fee"), "payment.fee")
        tax = _money(item.get("tax"), "payment.tax")
        created_at = _epoch(item.get("created_at"), "payment.created_at")
        if gross <= 0 or fee_including_tax < 0 or tax < 0:
            raise DemoEvidenceError(f"Payment {payment_id} has invalid negative monetary values")
        if tax > fee_including_tax:
            raise DemoEvidenceError(f"Payment {payment_id} has tax greater than fee")
        if str(item.get("currency") or "") != "INR":
            raise DemoEvidenceError("Demo settlement evidence supports INR only")
        captured.append(
            {
                **item,
                "_gross": gross,
                "_fee_including_tax": fee_including_tax,
                "_tax": tax,
                "_created_at": created_at,
            }
        )
    captured.sort(key=lambda item: (item["_created_at"], str(item["id"])))
    if not captured:
        raise DemoEvidenceError("No captured Test Mode payments are available")

    payment_by_id = {str(item["id"]): item for item in captured}
    payment_batch: dict[str, tuple[int, int]] = {}
    settlement_count = (len(captured) + DEMO_BATCH_SIZE - 1) // DEMO_BATCH_SIZE
    for batch_index in range(settlement_count):
        members = captured[batch_index * DEMO_BATCH_SIZE : (batch_index + 1) * DEMO_BATCH_SIZE]
        cutoff = max(int(item["_created_at"]) for item in members) + DEMO_SETTLEMENT_DELAY_SECONDS
        for item in members:
            payment_batch[str(item["id"])] = (batch_index, cutoff)

    processed_refunds: list[dict[str, Any]] = []
    refund_exclusions: list[dict[str, str]] = []
    seen_refund_ids: set[str] = set()
    refunded_by_payment: dict[str, int] = {}
    for item in refunds:
        refund_id = str(item.get("id") or "")
        if item.get("status") != "processed":
            refund_exclusions.append({"refund_id": refund_id, "reason": "STATUS_NOT_PROCESSED"})
            continue
        if not refund_id or refund_id in seen_refund_ids:
            raise DemoEvidenceError("Processed refunds contain missing or duplicate IDs")
        seen_refund_ids.add(refund_id)
        payment_id = str(item.get("payment_id") or "")
        payment = payment_by_id.get(payment_id)
        if payment is None:
            refund_exclusions.append(
                {"refund_id": refund_id, "reason": "PAYMENT_NOT_CAPTURED_IN_IMPORT"}
            )
            continue
        if str(item.get("currency") or "") != str(payment["currency"]):
            raise DemoEvidenceError(f"Refund {refund_id} currency differs from its payment")
        amount = _money(item.get("amount"), "refund.amount")
        created_at = _epoch(item.get("created_at"), "refund.created_at")
        if amount <= 0:
            raise DemoEvidenceError(f"Refund {refund_id} must have a positive amount")
        _batch_index, cutoff = payment_batch[payment_id]
        if created_at < int(payment["_created_at"]) or created_at > cutoff:
            refund_exclusions.append(
                {"refund_id": refund_id, "reason": "OUTSIDE_SYNTHETIC_SETTLEMENT_WINDOW"}
            )
            continue
        cumulative = refunded_by_payment.get(payment_id, 0) + amount
        if cumulative > int(payment["_gross"]):
            raise DemoEvidenceError(f"Processed refunds exceed payment {payment_id}")
        refunded_by_payment[payment_id] = cumulative
        processed_refunds.append({**item, "_amount": amount, "_created_at": created_at})
    processed_refunds.sort(key=lambda item: (item["_created_at"], str(item["id"])))
    refunds_by_payment: dict[str, list[dict[str, Any]]] = {}
    for refund in processed_refunds:
        refunds_by_payment.setdefault(str(refund["payment_id"]), []).append(refund)

    payment_rows: list[list[str]] = []
    refund_rows: list[list[str]] = []
    settlement_rows: list[list[str]] = []
    bank_rows: list[list[str]] = []
    ledger_rows: list[list[str]] = []
    for batch_index in range(settlement_count):
        members = captured[batch_index * DEMO_BATCH_SIZE : (batch_index + 1) * DEMO_BATCH_SIZE]
        settlement_id = _stable_id("setl", import_id, batch_index)
        utr = f"DEMO{hashlib.sha256(settlement_id.encode()).hexdigest()[:18].upper()}"
        created_values = [int(item["_created_at"]) for item in members]
        settled_at = max(created_values) + DEMO_SETTLEMENT_DELAY_SECONDS
        gross_total = 0
        fee_total = 0
        tax_total = 0
        refund_total = 0

        for payment in members:
            payment_id = str(payment["id"])
            gross = int(payment["_gross"])
            fee_including_tax = int(payment["_fee_including_tax"])
            tax = int(payment["_tax"])
            fee = fee_including_tax - tax
            currency = str(payment.get("currency") or "")
            created_at = int(payment["_created_at"])
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
                refund_amount = int(refund["_amount"])
                refund_created = int(refund["_created_at"])
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
                    _timestamp(settled_at + DEMO_BANK_POSTING_DELAY_SECONDS),
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
        "input_counts": {
            "payments_received": len(payments),
            "captured_payments_included": len(captured),
            "refunds_received": len(refunds),
            "processed_refunds_included": len(processed_refunds),
            "refunds_excluded": len(refund_exclusions),
        },
        "refund_exclusions": refund_exclusions,
        "synthetic_policy": {
            "policy_id": "argus-demo-settlement-v1",
            "batch_size": DEMO_BATCH_SIZE,
            "settlement_delay_seconds": DEMO_SETTLEMENT_DELAY_SECONDS,
            "bank_posting_delay_seconds": DEMO_BANK_POSTING_DELAY_SECONDS,
            "notice": "ARGUS synthetic demo policy; not Razorpay settlement policy.",
        },
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
