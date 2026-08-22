"""Residual case detectors for the four frozen exception categories (PRD 4.2).

Money semantics per case (documented sign convention: ``variance_paise`` is
observed minus expected over the case's reference scope):

- DUPLICATE_LEDGER_POSTING: variance = the extra posting's signed amount
  (observed ledger sum minus the single expected posting); affected = |amount|.
- MISSING_REFUND_POSTING: variance = +refund amount (the expected negative
  posting is absent, so observed is higher); affected = refund amount.
- SETTLEMENT_TIMING_WINDOW_SHIFT: variance = 0 (period attribution only,
  total economic value unchanged); affected = settlement net.
- AMBIGUOUS_EVIDENCE: variance is computed, never defaulted to zero.
  Missing-bank evidence carries variance = -settlement net; twin-settlement
  and refund-composition ambiguity keep aggregate variance 0 with a non-zero
  affected amount.

``proposed_delta_paise`` is always ``None`` in Phase 2: only a Phase 3
verifier PASS may derive a correction delta, and runtime code never reads
label deltas. Evidence sets are exactly the anchors of each phenomenon.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from app.domain.enums import CaseStatus, ExceptionCategory, ReasonCode, SourceType
from app.domain.records import (
    AcceptedRecords,
    LedgerEntryRecord,
    PaymentRecord,
    RefundRecord,
    SettlementRecord,
)
from app.reconciliation.engine import (
    EngineState,
    MatchGroup,
    ReconciliationEngine,
    composition_candidates,
)
from app.reconciliation.rules import (
    ACCOUNT_BANK,
    ACCOUNT_CLEARING,
    R_LEDGER_TO_SOURCE,
    R_REFUND_COMPOSITION,
)

CURRENCY_INR = "INR"


@dataclass(frozen=True)
class CaseEvidence:
    record_type: str
    record_id: str


@dataclass(frozen=True)
class CaseRecord:
    case_id: str
    category: ExceptionCategory
    status: CaseStatus
    variance_paise: int
    affected_amount_paise: int
    proposed_delta_paise: None
    currency: str
    summary: str
    reason_codes: tuple[str, ...]
    evidence: tuple[CaseEvidence, ...]
    variance_scope: str = "OTHER"

    def evidence_keys(self) -> set[tuple[str, str]]:
        return {(item.record_type, item.record_id) for item in self.evidence}


@dataclass(frozen=True)
class ReconciliationResult:
    matches: tuple[MatchGroup, ...]
    cases: tuple[CaseRecord, ...]
    matched_record_keys: frozenset[tuple[str, str]]
    case_evidence_keys: frozenset[tuple[str, str]]
    unaccounted_record_keys: frozenset[tuple[str, str]]

    @property
    def matched_record_count(self) -> int:
        return len(self.matched_record_keys)


def _case_id(category: ExceptionCategory, evidence: tuple[CaseEvidence, ...]) -> str:
    canonical = sorted([item.record_type, item.record_id] for item in evidence)
    digest = sha256(f"{category.value}|{canonical}".encode()).hexdigest()[:12]
    return f"case-{digest}"


def _make_case(
    category: ExceptionCategory,
    *,
    variance: int,
    affected: int,
    summary: str,
    reasons: tuple[str, ...],
    evidence: list[CaseEvidence],
    variance_scope: str = "OTHER",
) -> CaseRecord:
    deduped: list[CaseEvidence] = []
    seen: set[tuple[str, str]] = set()
    for item in evidence:
        key = (item.record_type, item.record_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    ordered = tuple(sorted(deduped, key=lambda item: (item.record_type, item.record_id)))
    return CaseRecord(
        case_id=_case_id(category, ordered),
        category=category,
        status=CaseStatus.OPEN,
        variance_paise=variance,
        affected_amount_paise=affected,
        proposed_delta_paise=None,
        currency=CURRENCY_INR,
        summary=summary,
        reason_codes=tuple(sorted(set(reasons))),
        evidence=ordered,
        variance_scope=variance_scope,
    )


def reconcile(records: AcceptedRecords) -> ReconciliationResult:
    """Run the engine, then convert every residual into an explicit case."""
    state = ReconciliationEngine().reconcile(records)
    cases: list[CaseRecord] = []
    cases.extend(_duplicate_cases(state))
    cases.extend(_missing_refund_cases(state))
    cases.extend(_timing_shift_cases(state))
    cases.extend(_ambiguous_cases(state))
    cases.extend(_broken_reference_cases(state))
    cases.extend(_generic_residual_cases(state, cases))

    matched = state.matched_record_keys()
    evidence_keys: set[tuple[str, str]] = set()
    for case in cases:
        evidence_keys |= case.evidence_keys()
    all_records = _all_record_keys(records)
    unaccounted = frozenset(
        key for key in all_records if key not in matched and key not in evidence_keys
    )
    ordered = tuple(
        sorted(
            cases,
            key=lambda case: (
                case.category.value,
                sorted(f"{item.record_type}:{item.record_id}" for item in case.evidence),
            ),
        )
    )
    return ReconciliationResult(
        matches=tuple(state.matches),
        cases=ordered,
        matched_record_keys=frozenset(matched),
        case_evidence_keys=frozenset(evidence_keys),
        unaccounted_record_keys=unaccounted,
    )


def _all_record_keys(records: AcceptedRecords) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    keys.update(("PAYMENT", payment.payment_id) for payment in records.payments)
    keys.update(("REFUND", refund.refund_id) for refund in records.refunds)
    keys.update(("SETTLEMENT", settlement.settlement_id) for settlement in records.settlements)
    keys.update(("BANK_ENTRY", credit.bank_entry_id) for credit in records.bank_entries)
    keys.update(("LEDGER_ENTRY", entry.ledger_entry_id) for entry in records.ledger_entries)
    return keys


def _maps(
    state: EngineState,
) -> tuple[
    dict[str, PaymentRecord],
    dict[str, RefundRecord],
    dict[str, SettlementRecord],
    dict[str, list[RefundRecord]],
]:
    records = state.records
    payments = {payment.payment_id: payment for payment in records.payments}
    refunds = {refund.refund_id: refund for refund in records.refunds}
    settlements = {s.settlement_id: s for s in records.settlements}
    refunds_by_payment: dict[str, list[RefundRecord]] = {}
    for refund in records.refunds:
        refunds_by_payment.setdefault(refund.payment_id, []).append(refund)
    return payments, refunds, settlements, refunds_by_payment


def _duplicate_semantics_unique(
    key: tuple[str, str, str, int, str],
    payments: dict[str, PaymentRecord],
    refunds: dict[str, RefundRecord],
    settlements: dict[str, SettlementRecord],
    refunds_by_payment: dict[str, list[RefundRecord]],
) -> bool:
    """A duplicate classification requires an unambiguous posting semantics.

    Aggregate deduction rows whose refund composition is non-unique are NOT
    duplicates: they belong to the composition-ambiguity detector instead.
    """
    source_type, reference, account, signed, _currency = key
    if source_type == SourceType.PAYMENT.value:
        payment = payments.get(reference)
        if payment is None or account != ACCOUNT_CLEARING:
            return False
        if signed == int(payment.net_paise):
            return True
        if signed < 0:
            candidates = composition_candidates(refunds_by_payment.get(reference, []), -signed)
            return len(candidates) == 1
        return False
    if source_type == SourceType.REFUND.value:
        refund = refunds.get(reference)
        return (
            refund is not None
            and account == ACCOUNT_CLEARING
            and signed == -int(refund.refund_amount_paise)
        )
    if source_type == SourceType.SETTLEMENT.value:
        settlement = settlements.get(reference)
        return (
            settlement is not None
            and account == ACCOUNT_BANK
            and signed == int(settlement.net_amount_paise)
        )
    return False


def _duplicate_cases(state: EngineState) -> list[CaseRecord]:
    payments, refunds, settlements, refunds_by_payment = _maps(state)
    groups: dict[tuple[str, str, str, int, str], list[LedgerEntryRecord]] = {}
    for ledger in state.records.ledger_entries:
        if ledger.source_reference is None or ledger.source_type is None:
            continue
        key = (
            ledger.source_type,
            ledger.source_reference,
            ledger.account_code,
            int(ledger.signed_amount_paise),
            ledger.currency,
        )
        groups.setdefault(key, []).append(ledger)
    cases: list[CaseRecord] = []
    for key in sorted(groups):
        rows = sorted(groups[key], key=lambda row: row.ledger_entry_id)
        if len(rows) < 2:
            continue
        if not _duplicate_semantics_unique(key, payments, refunds, settlements, refunds_by_payment):
            continue
        source_type, reference, _account, signed, _currency = key
        evidence = [CaseEvidence(source_type, reference)]
        evidence.extend(CaseEvidence("LEDGER_ENTRY", row.ledger_entry_id) for row in rows)
        cases.append(
            _make_case(
                ExceptionCategory.DUPLICATE_LEDGER_POSTING,
                variance=signed,
                affected=abs(signed),
                summary=(
                    f"duplicate ledger posting for {reference}: rows "
                    f"{[row.ledger_entry_id for row in rows]}"
                ),
                reasons=(ReasonCode.REFERENCE_CONFLICT.value,),
                evidence=evidence,
                variance_scope="LEDGER",
            )
        )
    return cases


def _ledger_matched_refunds(state: EngineState) -> set[str]:
    satisfied: set[str] = set()
    for group in state.matches:
        if group.rule_id not in (R_LEDGER_TO_SOURCE, R_REFUND_COMPOSITION):
            continue
        for member in group.members:
            if member.record_type == "REFUND":
                satisfied.add(member.record_id)
    return satisfied


def _missing_refund_cases(state: EngineState) -> list[CaseRecord]:
    payments, _refunds, settlements, _by_payment = _maps(state)
    satisfied = _ledger_matched_refunds(state)
    # Refunds whose postings exist as aggregate deduction rows with a
    # non-unique composition are covered by the composition-ambiguity case;
    # their ledger posting is present, only its attribution is ambiguous.
    ambiguous_refunds = {
        refund_id
        for finding in state.ambiguous_compositions.values()
        for refund_id in finding.candidate_refund_ids
    }
    cases: list[CaseRecord] = []
    for refund in state.records.refunds:
        if refund.refund_id in satisfied or refund.refund_id in ambiguous_refunds:
            continue
        parent = payments.get(refund.payment_id)
        if parent is None:
            continue  # broken reference detector owns refunds without parents
        evidence = [
            CaseEvidence("REFUND", refund.refund_id),
            CaseEvidence("PAYMENT", refund.payment_id),
        ]
        if refund.settlement_id is not None and refund.settlement_id in settlements:
            evidence.append(CaseEvidence("SETTLEMENT", refund.settlement_id))
        amount = int(refund.refund_amount_paise)
        cases.append(
            _make_case(
                ExceptionCategory.MISSING_REFUND_POSTING,
                variance=amount,
                affected=amount,
                summary=(
                    f"processed refund {refund.refund_id} has no ledger posting "
                    f"inside the posting window"
                ),
                reasons=(ReasonCode.MISSING_EVIDENCE.value,),
                evidence=evidence,
                variance_scope="LEDGER",
            )
        )
    return cases


def _timing_shift_cases(state: EngineState) -> list[CaseRecord]:
    cases: list[CaseRecord] = []
    for settlement in state.records.settlements:
        ledger = state.settlement_ledger.get(settlement.settlement_id)
        if ledger is None:
            continue
        booked = ledger.accounting_date
        if settlement.window_start_date <= booked <= settlement.window_end_date:
            continue
        net = int(settlement.net_amount_paise)
        cases.append(
            _make_case(
                ExceptionCategory.SETTLEMENT_TIMING_WINDOW_SHIFT,
                variance=0,
                affected=net,
                summary=(
                    f"settlement {settlement.settlement_id} booked {booked.isoformat()} "
                    f"outside window "
                    f"[{settlement.window_start_date.isoformat()}, "
                    f"{settlement.window_end_date.isoformat()}]"
                ),
                reasons=(ReasonCode.OUTSIDE_ALLOWED_WINDOW.value,),
                evidence=[
                    CaseEvidence("SETTLEMENT", settlement.settlement_id),
                    CaseEvidence("LEDGER_ENTRY", ledger.ledger_entry_id),
                ],
                variance_scope="LEDGER",
            )
        )
    return cases


def _ambiguous_cases(state: EngineState) -> list[CaseRecord]:
    cases: list[CaseRecord] = []
    settlements_by_id = {
        settlement.settlement_id: settlement for settlement in state.records.settlements
    }
    for twin in state.twin_groups:
        affected = sum(int(settlements_by_id[sid].net_amount_paise) for sid in twin.settlement_ids)
        evidence = [CaseEvidence("SETTLEMENT", sid) for sid in twin.settlement_ids]
        evidence += [CaseEvidence("BANK_ENTRY", bid) for bid in twin.bank_entry_ids]
        cases.append(
            _make_case(
                ExceptionCategory.AMBIGUOUS_EVIDENCE,
                variance=0,
                affected=affected,
                summary=(
                    "twin settlements share identical amount-window evidence with "
                    f"credits {list(twin.bank_entry_ids)}; a unique UTR is the "
                    "missing discriminator"
                ),
                reasons=(ReasonCode.NON_UNIQUE_EVIDENCE.value,),
                evidence=evidence,
                variance_scope="LEDGER",
            )
        )

    for missing in state.missing_bank:
        settlement = missing.settlement
        net = int(settlement.net_amount_paise)
        evidence = [CaseEvidence("SETTLEMENT", settlement.settlement_id)]
        ledger = state.settlement_ledger.get(settlement.settlement_id)
        if ledger is not None:
            evidence.append(CaseEvidence("LEDGER_ENTRY", ledger.ledger_entry_id))
        cases.append(
            _make_case(
                ExceptionCategory.AMBIGUOUS_EVIDENCE,
                variance=-net,
                affected=net,
                summary=(
                    f"settlement {settlement.settlement_id} has no matching bank "
                    "credit inside the posting window; required evidence is missing "
                    "and cannot be fabricated"
                ),
                reasons=(ReasonCode.MISSING_EVIDENCE.value,),
                evidence=evidence,
                variance_scope="BANK",
            )
        )

    for payment_id in sorted(state.ambiguous_compositions):
        finding = state.ambiguous_compositions[payment_id]
        affected = sum(abs(int(row.signed_amount_paise)) for row in finding.ledger_rows)
        evidence = [CaseEvidence("PAYMENT", payment_id)]
        evidence += [
            CaseEvidence("REFUND", refund_id) for refund_id in finding.candidate_refund_ids
        ]
        evidence += [
            CaseEvidence("LEDGER_ENTRY", row.ledger_entry_id) for row in finding.ledger_rows
        ]
        cases.append(
            _make_case(
                ExceptionCategory.AMBIGUOUS_EVIDENCE,
                variance=0,
                affected=affected,
                summary=(
                    f"aggregate deduction rows on payment {payment_id} admit "
                    "multiple refund compositions; the per-refund attribution "
                    "is non-unique"
                ),
                reasons=(ReasonCode.NON_UNIQUE_EVIDENCE.value,),
                evidence=evidence,
                variance_scope="LEDGER",
            )
        )

    for settlement, credit in state.utr_conflicts:
        difference = int(credit.signed_amount_paise) - int(settlement.net_amount_paise)
        cases.append(
            _make_case(
                ExceptionCategory.AMBIGUOUS_EVIDENCE,
                variance=difference,
                affected=abs(difference),
                summary=(
                    f"UTR {settlement.utr} links settlement "
                    f"{settlement.settlement_id} to credit "
                    f"{credit.bank_entry_id} with an incompatible amount"
                ),
                reasons=(
                    ReasonCode.AMOUNT_MISMATCH.value,
                    ReasonCode.REFERENCE_CONFLICT.value,
                ),
                evidence=[
                    CaseEvidence("SETTLEMENT", settlement.settlement_id),
                    CaseEvidence("BANK_ENTRY", credit.bank_entry_id),
                ],
                variance_scope="BANK",
            )
        )

    for settlement, difference in state.conservation_violations:
        cases.append(
            _make_case(
                ExceptionCategory.AMBIGUOUS_EVIDENCE,
                variance=difference,
                affected=abs(difference),
                summary=(
                    f"settlement {settlement.settlement_id} does not conserve member contributions"
                ),
                reasons=(ReasonCode.CONTROL_TOTAL_VIOLATION.value,),
                evidence=[CaseEvidence("SETTLEMENT", settlement.settlement_id)],
                variance_scope="LEDGER",
            )
        )
    return cases


def _broken_reference_cases(state: EngineState) -> list[CaseRecord]:
    cases: list[CaseRecord] = []
    for refund in state.parent_missing_refunds:
        amount = int(refund.refund_amount_paise)
        cases.append(
            _make_case(
                ExceptionCategory.AMBIGUOUS_EVIDENCE,
                variance=amount,
                affected=amount,
                summary=(
                    f"refund {refund.refund_id} references unknown payment {refund.payment_id}"
                ),
                reasons=(ReasonCode.MISSING_EVIDENCE.value,),
                evidence=[CaseEvidence("REFUND", refund.refund_id)],
                variance_scope="LEDGER",
            )
        )
    for payment in state.membership_missing_payments:
        net = int(payment.net_paise)
        cases.append(
            _make_case(
                ExceptionCategory.AMBIGUOUS_EVIDENCE,
                variance=net,
                affected=net,
                summary=(f"payment {payment.payment_id} has no resolvable settlement membership"),
                reasons=(ReasonCode.MISSING_EVIDENCE.value,),
                evidence=[CaseEvidence("PAYMENT", payment.payment_id)],
                variance_scope="LEDGER",
            )
        )
    return cases


def _generic_residual_cases(state: EngineState, existing: list[CaseRecord]) -> list[CaseRecord]:
    """Every accepted record unmatched and not yet case evidence gets a case."""
    covered: set[tuple[str, str]] = set()
    for case in existing:
        covered |= case.evidence_keys()
    matched = state.matched_record_keys()
    cases: list[CaseRecord] = []
    amounts = _record_amounts(state.records)
    for record_type, record_id in sorted(_all_record_keys(state.records)):
        key = (record_type, record_id)
        if key in matched or key in covered:
            continue
        amount = amounts.get(key, 0)
        cases.append(
            _make_case(
                ExceptionCategory.AMBIGUOUS_EVIDENCE,
                variance=0,
                affected=abs(amount),
                summary=(
                    f"{record_type} {record_id} remains unmatched after all "
                    "deterministic rules; no unique evidence supports a match"
                ),
                reasons=(ReasonCode.MISSING_EVIDENCE.value,),
                evidence=[CaseEvidence(record_type, record_id)],
            )
        )
    return cases


def _record_amounts(records: AcceptedRecords) -> dict[tuple[str, str], int]:
    amounts: dict[tuple[str, str], int] = {}
    for payment in records.payments:
        amounts[("PAYMENT", payment.payment_id)] = int(payment.net_paise)
    for refund in records.refunds:
        amounts[("REFUND", refund.refund_id)] = -int(refund.refund_amount_paise)
    for settlement in records.settlements:
        amounts[("SETTLEMENT", settlement.settlement_id)] = int(settlement.net_amount_paise)
    for credit in records.bank_entries:
        amounts[("BANK_ENTRY", credit.bank_entry_id)] = int(credit.signed_amount_paise)
    for entry in records.ledger_entries:
        amounts[("LEDGER_ENTRY", entry.ledger_entry_id)] = int(entry.signed_amount_paise)
    return amounts
