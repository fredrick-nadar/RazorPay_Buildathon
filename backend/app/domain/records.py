"""Normalized record schemas with source provenance (PRD 6.1-6.6).

Every accepted record carries a :class:`Provenance` pointing back at the
immutable physical source row: file, 1-based row number, source record id,
and a content hash computed by the importer over canonical JSON of ordered
``[column, raw_value]`` pairs. All money is signed integer paise; all
timestamps are timezone-aware UTC datetimes; accounting/value dates are
plain dates and stay distinct from event times.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.domain.money import Paise, add_paise, require_paise


@dataclass(frozen=True)
class Provenance:
    """Pointer from a normalized record to its immutable source row."""

    source_file: str
    source_row_number: int
    source_record_id: str
    content_hash: str


@dataclass(frozen=True)
class PaymentRecord:
    provenance: Provenance
    payment_id: str
    order_id: str | None
    status: str
    currency: str
    gross_amount_paise: Paise
    fee_paise: Paise
    tax_paise: Paise
    captured_at_utc: datetime
    settlement_id: str | None

    @property
    def net_paise(self) -> Paise:
        gross = int(self.gross_amount_paise)
        return Paise(gross - int(self.fee_paise) - int(self.tax_paise))


@dataclass(frozen=True)
class RefundRecord:
    provenance: Provenance
    refund_id: str
    payment_id: str
    status: str
    currency: str
    refund_amount_paise: Paise
    created_at_utc: datetime
    settlement_id: str | None


@dataclass(frozen=True)
class SettlementRecord:
    provenance: Provenance
    settlement_id: str
    settled_at_utc: datetime
    window_start_utc: datetime
    window_end_utc: datetime
    status: str
    currency: str
    gross_credit_paise: Paise
    fee_paise: Paise
    tax_paise: Paise
    adjustment_paise: Paise
    net_amount_paise: Paise
    utr: str | None

    @property
    def window_start_date(self) -> date:
        return self.window_start_utc.date()

    @property
    def window_end_date(self) -> date:
        return self.window_end_utc.date()


@dataclass(frozen=True)
class BankEntryRecord:
    provenance: Provenance
    bank_entry_id: str
    posted_at_utc: datetime
    value_date: date
    currency: str
    signed_amount_paise: Paise
    narration: str
    utr: str | None
    account_fingerprint: str


@dataclass(frozen=True)
class LedgerEntryRecord:
    provenance: Provenance
    ledger_entry_id: str
    account_code: str
    accounting_date: date
    currency: str
    signed_amount_paise: Paise
    source_reference: str | None
    source_type: str | None
    description: str
    entry_origin: str


@dataclass(frozen=True)
class AcceptedRecords:
    """Canonical (deduplicated, non-quarantined) records for one run."""

    payments: tuple[PaymentRecord, ...]
    refunds: tuple[RefundRecord, ...]
    settlements: tuple[SettlementRecord, ...]
    bank_entries: tuple[BankEntryRecord, ...]
    ledger_entries: tuple[LedgerEntryRecord, ...]

    def total_count(self) -> int:
        return (
            len(self.payments)
            + len(self.refunds)
            + len(self.settlements)
            + len(self.bank_entries)
            + len(self.ledger_entries)
        )


def sum_paise(values: list[Paise]) -> Paise:
    """Sum paise amounts with the same overflow guard as require_paise."""
    total = Paise(0)
    for value in values:
        total = add_paise(total, value)
    return total


def checked_paise(value: int) -> Paise:
    """Public re-check helper so callers never widen the money contract."""
    return require_paise(value)
