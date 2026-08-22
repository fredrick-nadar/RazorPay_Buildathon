"""Evidence snapshot: the frozen record and match view a verifier checks.

Built by re-running the pure, deterministic reconciliation engine over the
accepted records, so the snapshot observes exactly the engine state the
Phase 2 detectors observed without changing any Phase 2 module (PRD 9.1
``evidence_snapshot`` argument).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.records import (
    AcceptedRecords,
    BankEntryRecord,
    LedgerEntryRecord,
    PaymentRecord,
    RefundRecord,
    SettlementRecord,
)
from app.reconciliation.engine import (
    AmbiguousComposition,
    MatchGroup,
    ReconciliationEngine,
    TwinGroup,
)
from app.reconciliation.rules import R_LEDGER_TO_SOURCE, R_REFUND_COMPOSITION

Record = PaymentRecord | RefundRecord | SettlementRecord | BankEntryRecord | LedgerEntryRecord


@dataclass(frozen=True)
class EvidenceSnapshot:
    """Read-only view of records, matches, and engine findings."""

    payments: dict[str, PaymentRecord]
    refunds: dict[str, RefundRecord]
    settlements: dict[str, SettlementRecord]
    bank_entries: dict[str, BankEntryRecord]
    ledger_entries: dict[str, LedgerEntryRecord]
    matches: tuple[MatchGroup, ...]
    ledger_rows_by_source: dict[tuple[str, str], tuple[LedgerEntryRecord, ...]]
    posted_refund_ids: frozenset[str]
    twin_groups: tuple[TwinGroup, ...]
    twin_settlement_ids: frozenset[str]
    ambiguous_compositions: dict[str, AmbiguousComposition]
    ambiguous_refund_ids: frozenset[str]
    utr_conflict_settlement_ids: frozenset[str]
    missing_bank_settlement_ids: frozenset[str]
    conservation_violation_paise: dict[str, int]

    def contains(self, record_type: str, record_id: str) -> bool:
        return self.record(record_type, record_id) is not None

    def record(self, record_type: str, record_id: str) -> Record | None:
        if record_type == "PAYMENT":
            return self.payments.get(record_id)
        if record_type == "REFUND":
            return self.refunds.get(record_id)
        if record_type == "SETTLEMENT":
            return self.settlements.get(record_id)
        if record_type == "BANK_ENTRY":
            return self.bank_entries.get(record_id)
        if record_type == "LEDGER_ENTRY":
            return self.ledger_entries.get(record_id)
        return None

    def currency_of(self, record_type: str, record_id: str) -> str | None:
        found = self.record(record_type, record_id)
        return None if found is None else found.currency


def build_evidence_snapshot(records: AcceptedRecords) -> EvidenceSnapshot:
    """Derive the snapshot from a fresh engine pass (pure and deterministic)."""
    state = ReconciliationEngine().reconcile(records)

    ledger_rows_by_source: dict[tuple[str, str], list[LedgerEntryRecord]] = {}
    for entry in records.ledger_entries:
        if entry.source_reference is None or entry.source_type is None:
            continue
        key = (entry.source_type, entry.source_reference)
        ledger_rows_by_source.setdefault(key, []).append(entry)

    posted_refund_ids: set[str] = set()
    for group in state.matches:
        if group.rule_id not in (R_LEDGER_TO_SOURCE, R_REFUND_COMPOSITION):
            continue
        for member in group.members:
            if member.record_type == "REFUND":
                posted_refund_ids.add(member.record_id)

    ambiguous_compositions: dict[str, AmbiguousComposition] = dict(state.ambiguous_compositions)

    return EvidenceSnapshot(
        payments={item.payment_id: item for item in records.payments},
        refunds={item.refund_id: item for item in records.refunds},
        settlements={item.settlement_id: item for item in records.settlements},
        bank_entries={item.bank_entry_id: item for item in records.bank_entries},
        ledger_entries={item.ledger_entry_id: item for item in records.ledger_entries},
        matches=tuple(state.matches),
        ledger_rows_by_source={
            key: tuple(sorted(rows, key=lambda row: row.ledger_entry_id))
            for key, rows in ledger_rows_by_source.items()
        },
        posted_refund_ids=frozenset(posted_refund_ids),
        twin_groups=tuple(state.twin_groups),
        twin_settlement_ids=frozenset(
            settlement_id for group in state.twin_groups for settlement_id in group.settlement_ids
        ),
        ambiguous_compositions=ambiguous_compositions,
        ambiguous_refund_ids=frozenset(
            refund_id
            for finding in ambiguous_compositions.values()
            for refund_id in finding.candidate_refund_ids
        ),
        utr_conflict_settlement_ids=frozenset(
            settlement.settlement_id for settlement, _credit in state.utr_conflicts
        ),
        missing_bank_settlement_ids=frozenset(
            finding.settlement.settlement_id for finding in state.missing_bank
        ),
        conservation_violation_paise={
            settlement.settlement_id: difference
            for settlement, difference in state.conservation_violations
        },
    )
