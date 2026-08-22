"""Typed source adapters for the five input record types (PRD 8.1 step 1).

Each adapter validates one raw CSV row in a fixed precedence order and either
returns a fully normalized typed record or raises :class:`QuarantineSignal`
carrying a stable :class:`QuarantineReason`. Validation precedence per row:
shape -> required id -> currency -> status -> money -> timestamps -> dates ->
other required fields. The first failure wins, deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.domain.enums import QuarantineReason, SourceType
from app.domain.money import Paise
from app.domain.records import (
    BankEntryRecord,
    LedgerEntryRecord,
    PaymentRecord,
    Provenance,
    RefundRecord,
    SettlementRecord,
)
from app.importers.normalize import (
    FieldError,
    content_hash,
    normalize_currency,
    optional_text,
    parse_date,
    parse_paise,
    parse_timestamp,
    require_status,
    require_text,
)

PAYMENT_STATUS = frozenset({"CAPTURED"})
REFUND_STATUS = frozenset({"PROCESSED"})
SETTLEMENT_STATUS = frozenset({"PROCESSED"})
LEDGER_ORIGINS = frozenset({"IMPORTED"})


class QuarantineSignal(Exception):
    """Row-level validation failure with a stable quarantine reason."""

    def __init__(self, reason: QuarantineReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class AdapterSpec:
    file_stem: str
    source_type: SourceType
    columns: tuple[str, ...]
    id_column: str


PAYMENT_SPEC = AdapterSpec(
    file_stem="payments",
    source_type=SourceType.PAYMENT,
    columns=(
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
    id_column="payment_id",
)
REFUND_SPEC = AdapterSpec(
    file_stem="refunds",
    source_type=SourceType.REFUND,
    columns=(
        "refund_id",
        "payment_id",
        "status",
        "currency",
        "refund_amount",
        "created_at_utc",
        "settlement_id",
    ),
    id_column="refund_id",
)
SETTLEMENT_SPEC = AdapterSpec(
    file_stem="settlements",
    source_type=SourceType.SETTLEMENT,
    columns=(
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
    id_column="settlement_id",
)
BANK_SPEC = AdapterSpec(
    file_stem="bank_entries",
    source_type=SourceType.BANK_ENTRY,
    columns=(
        "bank_entry_id",
        "posted_at_utc",
        "value_date",
        "currency",
        "signed_amount",
        "narration",
        "utr",
        "account_fingerprint",
    ),
    id_column="bank_entry_id",
)
LEDGER_SPEC = AdapterSpec(
    file_stem="ledger_entries",
    source_type=SourceType.LEDGER_ENTRY,
    columns=(
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
    id_column="ledger_entry_id",
)

ADAPTER_SPECS: tuple[AdapterSpec, ...] = (
    PAYMENT_SPEC,
    REFUND_SPEC,
    SETTLEMENT_SPEC,
    BANK_SPEC,
    LEDGER_SPEC,
)


def _signal(reason: QuarantineReason, exc: FieldError) -> QuarantineSignal:
    return QuarantineSignal(reason, str(exc))


def _check_id(row: dict[str, str], column: str) -> str:
    try:
        return require_text(row, column)
    except FieldError as exc:
        raise _signal(QuarantineReason.MISSING_REQUIRED_FIELD, exc) from exc


def _check_currency(row: dict[str, str]) -> str:
    try:
        return normalize_currency(row.get("currency", "")).value
    except FieldError as exc:
        raise _signal(QuarantineReason.UNSUPPORTED_CURRENCY, exc) from exc


def _check_status(row: dict[str, str], allowed: frozenset[str]) -> str:
    try:
        return require_status(row, "status", allowed)
    except FieldError as exc:
        raise _signal(QuarantineReason.UNKNOWN_STATUS, exc) from exc


def _check_money(row: dict[str, str], column: str) -> Paise:
    try:
        return parse_paise(row.get(column, ""))
    except FieldError as exc:
        raise _signal(QuarantineReason.INVALID_MONEY, exc) from exc


def _check_timestamp(row: dict[str, str], column: str) -> datetime:
    try:
        return parse_timestamp(row.get(column, ""))
    except FieldError as exc:
        raise _signal(QuarantineReason.INVALID_TIMESTAMP, exc) from exc


def _check_date(row: dict[str, str], column: str) -> date:
    try:
        return parse_date(row.get(column, ""))
    except FieldError as exc:
        raise _signal(QuarantineReason.INVALID_DATE, exc) from exc


def _check_required(row: dict[str, str], column: str) -> str:
    try:
        return require_text(row, column)
    except FieldError as exc:
        raise _signal(QuarantineReason.MISSING_REQUIRED_FIELD, exc) from exc


def parse_payment_row(row: dict[str, str], row_number: int, source_file: str) -> PaymentRecord:
    record_id = _check_id(row, PAYMENT_SPEC.id_column)
    currency = _check_currency(row)
    status = _check_status(row, PAYMENT_STATUS)
    gross = _check_money(row, "gross_amount")
    fee = _check_money(row, "fee_amount")
    tax = _check_money(row, "tax_amount")
    captured = _check_timestamp(row, "captured_at_utc")
    provenance = Provenance(
        source_file=source_file,
        source_row_number=row_number,
        source_record_id=record_id,
        content_hash=content_hash(PAYMENT_SPEC.columns, row),
    )
    return PaymentRecord(
        provenance=provenance,
        payment_id=record_id,
        order_id=optional_text(row, "order_id"),
        status=status,
        currency=currency,
        gross_amount_paise=gross,
        fee_paise=fee,
        tax_paise=tax,
        captured_at_utc=captured,
        settlement_id=optional_text(row, "settlement_id"),
    )


def parse_refund_row(row: dict[str, str], row_number: int, source_file: str) -> RefundRecord:
    record_id = _check_id(row, REFUND_SPEC.id_column)
    currency = _check_currency(row)
    status = _check_status(row, REFUND_STATUS)
    amount = _check_money(row, "refund_amount")
    created = _check_timestamp(row, "created_at_utc")
    payment_id = _check_required(row, "payment_id")
    provenance = Provenance(
        source_file=source_file,
        source_row_number=row_number,
        source_record_id=record_id,
        content_hash=content_hash(REFUND_SPEC.columns, row),
    )
    return RefundRecord(
        provenance=provenance,
        refund_id=record_id,
        payment_id=payment_id,
        status=status,
        currency=currency,
        refund_amount_paise=amount,
        created_at_utc=created,
        settlement_id=optional_text(row, "settlement_id"),
    )


def parse_settlement_row(
    row: dict[str, str], row_number: int, source_file: str
) -> SettlementRecord:
    record_id = _check_id(row, SETTLEMENT_SPEC.id_column)
    currency = _check_currency(row)
    status = _check_status(row, SETTLEMENT_STATUS)
    gross = _check_money(row, "gross_credit")
    fee = _check_money(row, "fee_amount")
    tax = _check_money(row, "tax_amount")
    adjustment = _check_money(row, "adjustment_amount")
    net = _check_money(row, "net_amount")
    settled = _check_timestamp(row, "settled_at_utc")
    window_start = _check_timestamp(row, "window_start_utc")
    window_end = _check_timestamp(row, "window_end_utc")
    provenance = Provenance(
        source_file=source_file,
        source_row_number=row_number,
        source_record_id=record_id,
        content_hash=content_hash(SETTLEMENT_SPEC.columns, row),
    )
    return SettlementRecord(
        provenance=provenance,
        settlement_id=record_id,
        settled_at_utc=settled,
        window_start_utc=window_start,
        window_end_utc=window_end,
        status=status,
        currency=currency,
        gross_credit_paise=gross,
        fee_paise=fee,
        tax_paise=tax,
        adjustment_paise=adjustment,
        net_amount_paise=net,
        utr=optional_text(row, "utr"),
    )


def parse_bank_row(row: dict[str, str], row_number: int, source_file: str) -> BankEntryRecord:
    record_id = _check_id(row, BANK_SPEC.id_column)
    currency = _check_currency(row)
    amount = _check_money(row, "signed_amount")
    posted = _check_timestamp(row, "posted_at_utc")
    value_date = _check_date(row, "value_date")
    fingerprint = _check_required(row, "account_fingerprint")
    provenance = Provenance(
        source_file=source_file,
        source_row_number=row_number,
        source_record_id=record_id,
        content_hash=content_hash(BANK_SPEC.columns, row),
    )
    return BankEntryRecord(
        provenance=provenance,
        bank_entry_id=record_id,
        posted_at_utc=posted,
        value_date=value_date,
        currency=currency,
        signed_amount_paise=amount,
        narration=row.get("narration", ""),
        utr=optional_text(row, "utr"),
        account_fingerprint=fingerprint,
    )


def parse_ledger_row(row: dict[str, str], row_number: int, source_file: str) -> LedgerEntryRecord:
    record_id = _check_id(row, LEDGER_SPEC.id_column)
    currency = _check_currency(row)
    amount = _check_money(row, "signed_amount")
    accounting_date = _check_date(row, "accounting_date")
    account_code = _check_required(row, "account_code")
    origin = _check_required(row, "entry_origin")
    if origin not in LEDGER_ORIGINS:
        raise QuarantineSignal(
            QuarantineReason.UNKNOWN_STATUS,
            f"unsupported entry_origin {origin!r}",
        )
    provenance = Provenance(
        source_file=source_file,
        source_row_number=row_number,
        source_record_id=record_id,
        content_hash=content_hash(LEDGER_SPEC.columns, row),
    )
    return LedgerEntryRecord(
        provenance=provenance,
        ledger_entry_id=record_id,
        account_code=account_code,
        accounting_date=accounting_date,
        currency=currency,
        signed_amount_paise=amount,
        source_reference=optional_text(row, "source_reference"),
        source_type=optional_text(row, "source_type"),
        description=row.get("description", ""),
        entry_origin=origin,
    )
