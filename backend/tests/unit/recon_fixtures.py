"""Hand-built record fixtures for reconciliation unit tests (not collected).

Small, explicit corpora that isolate one matching rule each: identifiers
outranking amounts, many-to-one membership, consumption slots, and tie
behaviour. Timestamps are chosen so window checks behave predictably.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.money import Paise
from app.domain.records import (
    AcceptedRecords,
    BankEntryRecord,
    LedgerEntryRecord,
    PaymentRecord,
    Provenance,
    RefundRecord,
    SettlementRecord,
)


def _ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _provenance(source_type: str, record_id: str, row_number: int) -> Provenance:
    return Provenance(
        source_file=f"inputs/{source_type}.csv",
        source_row_number=row_number,
        source_record_id=record_id,
        content_hash=f"hash-{source_type}-{record_id}",
    )


def payment(
    payment_id: str,
    *,
    gross: int,
    fee: int = 0,
    tax: int = 0,
    captured: str = "2026-03-02T10:00:00Z",
    settlement_id: str | None = "stl_S000000001",
    row_number: int = 1,
) -> PaymentRecord:
    return PaymentRecord(
        provenance=_provenance("payments", payment_id, row_number),
        payment_id=payment_id,
        order_id=f"order_{payment_id}",
        status="CAPTURED",
        currency="INR",
        gross_amount_paise=Paise(gross),
        fee_paise=Paise(fee),
        tax_paise=Paise(tax),
        captured_at_utc=_ts(captured),
        settlement_id=settlement_id,
    )


def refund(
    refund_id: str,
    *,
    payment_id: str,
    amount: int,
    created: str = "2026-03-02T12:00:00Z",
    settlement_id: str | None = "stl_S000000001",
) -> RefundRecord:
    return RefundRecord(
        provenance=_provenance("refunds", refund_id, 1),
        refund_id=refund_id,
        payment_id=payment_id,
        status="PROCESSED",
        currency="INR",
        refund_amount_paise=Paise(amount),
        created_at_utc=_ts(created),
        settlement_id=settlement_id,
    )


def settlement(
    settlement_id: str,
    *,
    net: int,
    gross: int | None = None,
    adjustment: int = 0,
    window: tuple[str, str] = ("2026-03-02T00:00:00Z", "2026-03-03T00:00:00Z"),
    settled: str = "2026-03-03T04:00:00Z",
    utr: str | None = None,
) -> SettlementRecord:
    total = net - adjustment
    return SettlementRecord(
        provenance=_provenance("settlements", settlement_id, 1),
        settlement_id=settlement_id,
        settled_at_utc=_ts(settled),
        window_start_utc=_ts(window[0]),
        window_end_utc=_ts(window[1]),
        status="PROCESSED",
        currency="INR",
        gross_credit_paise=Paise(gross if gross is not None else total),
        fee_paise=Paise(0),
        tax_paise=Paise(0),
        adjustment_paise=Paise(adjustment),
        net_amount_paise=Paise(net),
        utr=utr,
    )


def bank_credit(
    bank_entry_id: str,
    *,
    amount: int,
    posted: str = "2026-03-03T05:00:00Z",
    utr: str | None = None,
) -> BankEntryRecord:
    from datetime import date

    return BankEntryRecord(
        provenance=_provenance("bank_entries", bank_entry_id, 1),
        bank_entry_id=bank_entry_id,
        posted_at_utc=_ts(posted),
        value_date=date(2026, 3, 3),
        currency="INR",
        signed_amount_paise=Paise(amount),
        narration="NEFT CR",
        utr=utr,
        account_fingerprint="FP-ARGUS-DEMO-01",
    )


def ledger_row(
    ledger_entry_id: str,
    *,
    source_type: str,
    source_reference: str,
    amount: int,
    account: str,
    accounting_date: str = "2026-03-02",
) -> LedgerEntryRecord:
    from datetime import date

    year, month, day = (int(part) for part in accounting_date.split("-"))
    return LedgerEntryRecord(
        provenance=_provenance("ledger_entries", ledger_entry_id, 1),
        ledger_entry_id=ledger_entry_id,
        account_code=account,
        accounting_date=date(year, month, day),
        currency="INR",
        signed_amount_paise=Paise(amount),
        source_reference=source_reference,
        source_type=source_type,
        description="posting",
        entry_origin="IMPORTED",
    )


def records(
    payments: list[PaymentRecord] | None = None,
    refunds: list[RefundRecord] | None = None,
    settlements: list[SettlementRecord] | None = None,
    bank_entries: list[BankEntryRecord] | None = None,
    ledger_entries: list[LedgerEntryRecord] | None = None,
) -> AcceptedRecords:
    return AcceptedRecords(
        payments=tuple(sorted(payments or [], key=lambda item: item.payment_id)),
        refunds=tuple(sorted(refunds or [], key=lambda item: item.refund_id)),
        settlements=tuple(sorted(settlements or [], key=lambda item: item.settlement_id)),
        bank_entries=tuple(sorted(bank_entries or [], key=lambda item: item.bank_entry_id)),
        ledger_entries=tuple(sorted(ledger_entries or [], key=lambda item: item.ledger_entry_id)),
    )
