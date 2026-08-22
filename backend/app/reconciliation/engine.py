"""Deterministic matching engine with typed consumption slots (PRD 8.2, 8.3).

The engine applies rules strictly strongest-first over id-sorted records:

1. exact cross-file identifiers (refund->payment, settlement membership,
   ledger->source with amount semantics);
2. exact UTR plus compatible amount for settlement->bank;
3. unique amount within the +/-24h posting window for UTR-less settlements
   (unique in both directions);
4. unique refund composition for aggregate deduction rows.

Amounts are never sufficient by themselves: every amount-based rule also
requires a strong identifier or a uniqueness proof, and any tie becomes an
ambiguous finding for the case detectors instead of a guessed match.

Consumption is keyed by typed slot, so a payment can simultaneously hold
settlement membership, refund-parent, and ledger-source relationships while
bank credits and individual ledger entries remain strictly exclusive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from hashlib import sha256
from itertools import combinations

from app.domain.enums import RelationshipType, SourceType
from app.domain.records import (
    AcceptedRecords,
    BankEntryRecord,
    LedgerEntryRecord,
    PaymentRecord,
    RefundRecord,
    SettlementRecord,
)
from app.reconciliation.rules import (
    ACCOUNT_BANK,
    ACCOUNT_CLEARING,
    BANK_POSTING_WINDOW_S,
    MAX_COMPOSITION_REFUNDS,
    R_LEDGER_TO_SOURCE,
    R_PAYMENT_TO_SETTLEMENT,
    R_REFUND_COMPOSITION,
    R_REFUND_TO_PAYMENT,
    R_SETTLEMENT_BANK_UNIQUE,
    R_SETTLEMENT_BANK_UTR,
    RULE_VERSIONS,
    SLOT_BANK_CREDIT_MATCH,
    SLOT_LEDGER_SOURCE_MATCH,
    SLOT_REFUND_COMPOSITION,
    SLOT_REFUND_PARENT,
    SLOT_SETTLEMENT_MEMBERSHIP,
)


@dataclass(frozen=True)
class MatchMember:
    record_type: str
    record_id: str
    role: str
    signed_contribution_paise: int


@dataclass(frozen=True)
class MatchGroup:
    match_id: str
    relationship_type: RelationshipType
    rule_id: str
    rule_version: str
    amount_paise: int
    members: tuple[MatchMember, ...]

    def member_keys(self) -> set[tuple[str, str]]:
        return {(member.record_type, member.record_id) for member in self.members}


@dataclass(frozen=True)
class AmbiguousComposition:
    payment_id: str
    ledger_rows: tuple[LedgerEntryRecord, ...]
    candidate_refund_ids: tuple[str, ...]


@dataclass(frozen=True)
class TwinGroup:
    settlement_ids: tuple[str, ...]
    bank_entry_ids: tuple[str, ...]


@dataclass(frozen=True)
class MissingBankEvidence:
    settlement: SettlementRecord
    has_utr: bool


@dataclass
class EngineState:
    """Matching outputs plus structured findings consumed by the detectors."""

    records: AcceptedRecords
    matches: list[MatchGroup] = field(default_factory=list)
    used_slots: set[str] = field(default_factory=set)
    settlement_ledger: dict[str, LedgerEntryRecord] = field(default_factory=dict)
    composition_covered_refunds: set[str] = field(default_factory=set)
    ambiguous_compositions: dict[str, AmbiguousComposition] = field(default_factory=dict)
    utr_conflicts: list[tuple[SettlementRecord, BankEntryRecord]] = field(default_factory=list)
    twin_groups: list[TwinGroup] = field(default_factory=list)
    missing_bank: list[MissingBankEvidence] = field(default_factory=list)
    membership_missing_payments: list[PaymentRecord] = field(default_factory=list)
    parent_missing_refunds: list[RefundRecord] = field(default_factory=list)
    conservation_violations: list[tuple[SettlementRecord, int]] = field(default_factory=list)

    def matched_record_keys(self) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for group in self.matches:
            keys |= group.member_keys()
        return keys


def _slot(slot_name: str, record_type: str, record_id: str, kind: str = "") -> str:
    return f"{slot_name}:{record_type}:{record_id}:{kind}"


def _match_id(
    relationship: RelationshipType, rule_id: str, members: tuple[MatchMember, ...]
) -> str:
    canonical = [
        [member.record_type, member.record_id, member.signed_contribution_paise]
        for member in sorted(members, key=lambda member: (member.record_type, member.record_id))
    ]
    digest = sha256(f"{relationship.value}|{rule_id}|{canonical}".encode()).hexdigest()[:12]
    return f"match-{digest}"


def _pair_group(
    relationship: RelationshipType,
    rule_id: str,
    amount: int,
    members: tuple[MatchMember, ...],
) -> MatchGroup:
    return MatchGroup(
        match_id=_match_id(relationship, rule_id, members),
        relationship_type=relationship,
        rule_id=rule_id,
        rule_version=RULE_VERSIONS[rule_id],
        amount_paise=amount,
        members=members,
    )


def composition_candidates(refunds: list[RefundRecord], target_paise: int) -> list[tuple[str, ...]]:
    """Refund subsets summing exactly to ``target_paise`` (bounded subset-sum)."""
    if not refunds or len(refunds) > MAX_COMPOSITION_REFUNDS:
        return []
    hits: list[tuple[str, ...]] = []
    ids = [refund.refund_id for refund in refunds]
    amounts = [int(refund.refund_amount_paise) for refund in refunds]
    for size in range(1, len(ids) + 1):
        for combo in combinations(range(len(ids)), size):
            if sum(amounts[index] for index in combo) == target_paise:
                hits.append(tuple(sorted(ids[index] for index in combo)))
    return sorted(hits)


def _has_multiple_compositions(refunds: list[RefundRecord], target_paise: int) -> bool:
    return len(composition_candidates(refunds, target_paise)) >= 2


class ReconciliationEngine:
    """Applies the matching hierarchy; produces matches and detector findings."""

    def reconcile(self, records: AcceptedRecords) -> EngineState:
        state = EngineState(records=records)
        payments_by_id = {payment.payment_id: payment for payment in records.payments}
        refunds_by_id = {refund.refund_id: refund for refund in records.refunds}
        refunds_by_payment: dict[str, list[RefundRecord]] = {}
        for refund in records.refunds:
            refunds_by_payment.setdefault(refund.payment_id, []).append(refund)
        for group in refunds_by_payment.values():
            group.sort(key=lambda refund: refund.refund_id)
        settlements_by_id = {
            settlement.settlement_id: settlement for settlement in records.settlements
        }

        self._match_refund_to_payment(state, payments_by_id)
        self._match_settlement_membership(state, settlements_by_id)
        self._match_ledger_to_source(
            state, payments_by_id, refunds_by_id, refunds_by_payment, settlements_by_id
        )
        self._match_settlement_to_bank(state)
        return state

    # -- tier 1: exact cross-file identifiers ------------------------------

    def _match_refund_to_payment(
        self, state: EngineState, payments_by_id: dict[str, PaymentRecord]
    ) -> None:
        for refund in state.records.refunds:
            slot = _slot(SLOT_REFUND_PARENT, "REFUND", refund.refund_id)
            if slot in state.used_slots:
                continue
            parent = payments_by_id.get(refund.payment_id)
            if parent is None:
                state.parent_missing_refunds.append(refund)
                continue
            amount = int(refund.refund_amount_paise)
            members = (
                MatchMember("REFUND", refund.refund_id, "CHILD", -amount),
                MatchMember("PAYMENT", parent.payment_id, "PARENT", amount),
            )
            state.used_slots.add(slot)
            state.matches.append(
                _pair_group(
                    RelationshipType.REFUND_OF_PAYMENT,
                    R_REFUND_TO_PAYMENT,
                    amount,
                    members,
                )
            )

    def _match_settlement_membership(
        self, state: EngineState, settlements_by_id: dict[str, SettlementRecord]
    ) -> None:
        payments_by_settlement: dict[str, list[PaymentRecord]] = {}
        for payment in state.records.payments:
            if payment.settlement_id is None or payment.settlement_id not in settlements_by_id:
                state.membership_missing_payments.append(payment)
                continue
            payments_by_settlement.setdefault(payment.settlement_id, []).append(payment)
        refunds_by_settlement: dict[str, list[RefundRecord]] = {}
        for refund in state.records.refunds:
            if refund.settlement_id is None or refund.settlement_id not in settlements_by_id:
                continue
            refunds_by_settlement.setdefault(refund.settlement_id, []).append(refund)

        for settlement in state.records.settlements:
            members_payments = sorted(
                payments_by_settlement.get(settlement.settlement_id, []),
                key=lambda payment: payment.payment_id,
            )
            members_refunds = sorted(
                refunds_by_settlement.get(settlement.settlement_id, []),
                key=lambda refund: refund.refund_id,
            )
            if not members_payments and not members_refunds:
                continue
            payment_slots = [
                _slot(SLOT_SETTLEMENT_MEMBERSHIP, "PAYMENT", payment.payment_id)
                for payment in members_payments
            ]
            refund_slots = [
                _slot(SLOT_SETTLEMENT_MEMBERSHIP, "REFUND", refund.refund_id)
                for refund in members_refunds
            ]
            if any(slot in state.used_slots for slot in payment_slots + refund_slots):
                continue
            members: list[MatchMember] = []
            total = 0
            for payment in members_payments:
                contribution = int(payment.net_paise)
                members.append(
                    MatchMember("PAYMENT", payment.payment_id, "CONTRIBUTOR", contribution)
                )
                total += contribution
            for refund in members_refunds:
                contribution = -int(refund.refund_amount_paise)
                members.append(MatchMember("REFUND", refund.refund_id, "ADJUSTMENT", contribution))
                total += contribution
            net = int(settlement.net_amount_paise)
            if total != net:
                # Conservation is broken: no match group is created; every
                # member stays visible to the residual sweep and the violation
                # becomes an explicit case.
                state.conservation_violations.append((settlement, total - net))
                continue
            state.used_slots.update(payment_slots + refund_slots)
            # The settlement itself joins its aggregation group as a
            # zero-contribution TARGET so the group is self-describing and the
            # evidence graph can anchor member edges on it.
            members.append(MatchMember("SETTLEMENT", settlement.settlement_id, "TARGET", 0))
            state.matches.append(
                _pair_group(
                    RelationshipType.MEMBER_OF_SETTLEMENT,
                    R_PAYMENT_TO_SETTLEMENT,
                    net,
                    tuple(members),
                )
            )

    # -- ledger -> source with amount semantics ----------------------------

    def _match_ledger_to_source(
        self,
        state: EngineState,
        payments_by_id: dict[str, PaymentRecord],
        refunds_by_id: dict[str, RefundRecord],
        refunds_by_payment: dict[str, list[RefundRecord]],
        settlements_by_id: dict[str, SettlementRecord],
    ) -> None:
        for ledger in state.records.ledger_entries:
            row_slot = _slot(SLOT_LEDGER_SOURCE_MATCH, "LEDGER_ENTRY", ledger.ledger_entry_id)
            if row_slot in state.used_slots:
                continue
            if ledger.source_reference is None or ledger.source_type is None:
                continue
            signed = int(ledger.signed_amount_paise)
            source_type = ledger.source_type
            reference = ledger.source_reference

            if source_type == SourceType.REFUND.value:
                refund = refunds_by_id.get(reference)
                if refund is None:
                    continue
                expected = -int(refund.refund_amount_paise)
                if ledger.account_code != ACCOUNT_CLEARING or signed != expected:
                    continue
                state.used_slots.add(row_slot)
                state.matches.append(
                    _pair_group(
                        RelationshipType.LEDGER_SOURCE,
                        R_LEDGER_TO_SOURCE,
                        abs(signed),
                        (
                            MatchMember("REFUND", refund.refund_id, "SOURCE", -signed),
                            MatchMember("LEDGER_ENTRY", ledger.ledger_entry_id, "BOOKING", signed),
                        ),
                    )
                )
            elif source_type == SourceType.SETTLEMENT.value:
                settlement = settlements_by_id.get(reference)
                if settlement is None:
                    continue
                expected = int(settlement.net_amount_paise)
                if ledger.account_code != ACCOUNT_BANK or signed != expected:
                    continue
                state.used_slots.add(row_slot)
                state.settlement_ledger[settlement.settlement_id] = ledger
                state.matches.append(
                    _pair_group(
                        RelationshipType.LEDGER_SOURCE,
                        R_LEDGER_TO_SOURCE,
                        abs(signed),
                        (
                            MatchMember("SETTLEMENT", settlement.settlement_id, "SOURCE", -signed),
                            MatchMember("LEDGER_ENTRY", ledger.ledger_entry_id, "BOOKING", signed),
                        ),
                    )
                )
            elif source_type == SourceType.PAYMENT.value:
                payment = payments_by_id.get(reference)
                if payment is None or ledger.account_code != ACCOUNT_CLEARING:
                    continue
                net = int(payment.net_paise)
                if signed == net:
                    kind_slot = _slot(
                        SLOT_LEDGER_SOURCE_MATCH, "PAYMENT", payment.payment_id, "NET"
                    )
                    if kind_slot in state.used_slots:
                        continue
                    state.used_slots.add(row_slot)
                    state.used_slots.add(kind_slot)
                    state.matches.append(
                        _pair_group(
                            RelationshipType.LEDGER_SOURCE,
                            R_LEDGER_TO_SOURCE,
                            abs(signed),
                            (
                                MatchMember("PAYMENT", payment.payment_id, "SOURCE", -signed),
                                MatchMember(
                                    "LEDGER_ENTRY",
                                    ledger.ledger_entry_id,
                                    "BOOKING",
                                    signed,
                                ),
                            ),
                        )
                    )
                elif signed < 0:
                    self._resolve_composition(state, ledger, payment, refunds_by_payment)
            # Unknown source_type values fall through to the residual sweep.

    def _resolve_composition(
        self,
        state: EngineState,
        ledger: LedgerEntryRecord,
        payment: PaymentRecord,
        refunds_by_payment: dict[str, list[RefundRecord]],
    ) -> None:
        refunds = refunds_by_payment.get(payment.payment_id, [])
        target = -int(ledger.signed_amount_paise)
        candidates = composition_candidates(refunds, target)
        if len(candidates) == 1:
            composition = candidates[0]
            refund_slots = [
                _slot(SLOT_REFUND_COMPOSITION, "REFUND", refund_id) for refund_id in composition
            ]
            if any(slot in state.used_slots for slot in refund_slots):
                return
            amounts = {refund.refund_id: int(refund.refund_amount_paise) for refund in refunds}
            members: list[MatchMember] = [
                MatchMember(
                    "LEDGER_ENTRY",
                    ledger.ledger_entry_id,
                    "BOOKING",
                    int(ledger.signed_amount_paise),
                )
            ]
            for refund_id in composition:
                members.append(MatchMember("REFUND", refund_id, "COMPONENT", amounts[refund_id]))
            row_slot = _slot(SLOT_LEDGER_SOURCE_MATCH, "LEDGER_ENTRY", ledger.ledger_entry_id)
            state.used_slots.add(row_slot)
            state.used_slots.update(refund_slots)
            state.composition_covered_refunds.update(composition)
            state.matches.append(
                _pair_group(
                    RelationshipType.LEDGER_SOURCE,
                    R_REFUND_COMPOSITION,
                    target,
                    tuple(members),
                )
            )
        elif len(candidates) >= 2:
            participating = sorted({refund_id for group in candidates for refund_id in group})
            rows = tuple(
                row
                for row in state.records.ledger_entries
                if row.source_reference == payment.payment_id
                and row.source_type == SourceType.PAYMENT.value
                and int(row.signed_amount_paise) < 0
                and _has_multiple_compositions(refunds, -int(row.signed_amount_paise))
            )
            state.ambiguous_compositions[payment.payment_id] = AmbiguousComposition(
                payment_id=payment.payment_id,
                ledger_rows=rows,
                candidate_refund_ids=tuple(participating),
            )
        # len(candidates) == 0: unexplained deduction row -> residual sweep.

    # -- settlement -> bank -------------------------------------------------

    def _match_settlement_to_bank(self, state: EngineState) -> None:
        tolerance = timedelta(seconds=BANK_POSTING_WINDOW_S)
        credits_by_utr: dict[str, list[BankEntryRecord]] = {}
        for credit in state.records.bank_entries:
            if credit.utr is not None:
                credits_by_utr.setdefault(credit.utr, []).append(credit)

        def bank_slot(record_id: str) -> str:
            return _slot(SLOT_BANK_CREDIT_MATCH, "BANK_ENTRY", record_id)

        def settlement_slot(record_id: str) -> str:
            return _slot(SLOT_BANK_CREDIT_MATCH, "SETTLEMENT", record_id)

        def record_pair(
            settlement: SettlementRecord, credit: BankEntryRecord, rule_id: str
        ) -> MatchGroup:
            net = int(settlement.net_amount_paise)
            members = (
                MatchMember("SETTLEMENT", settlement.settlement_id, "SOURCE", -net),
                MatchMember(
                    "BANK_ENTRY",
                    credit.bank_entry_id,
                    "CREDIT",
                    int(credit.signed_amount_paise),
                ),
            )
            return _pair_group(
                RelationshipType.SETTLEMENT_BANK_CREDIT,
                rule_id,
                net,
                members,
            )

        # Tier 2 first: exact UTR + compatible amount.
        for settlement in state.records.settlements:
            if settlement.utr is None:
                continue
            credits = credits_by_utr.get(settlement.utr, [])
            if not credits:
                state.missing_bank.append(MissingBankEvidence(settlement=settlement, has_utr=True))
                continue
            credit = credits[0]
            net = int(settlement.net_amount_paise)
            if int(credit.signed_amount_paise) != net or len(credits) > 1:
                state.utr_conflicts.append((settlement, credit))
                continue
            if bank_slot(credit.bank_entry_id) in state.used_slots:
                continue
            state.used_slots.add(bank_slot(credit.bank_entry_id))
            state.used_slots.add(settlement_slot(settlement.settlement_id))
            state.matches.append(record_pair(settlement, credit, R_SETTLEMENT_BANK_UTR))

        # Tier 3: unique amount within the window, UTR absent on both sides.
        def candidate_credits(settlement: SettlementRecord) -> list[BankEntryRecord]:
            net = int(settlement.net_amount_paise)
            low = settlement.window_start_utc - tolerance
            high = settlement.window_end_utc + tolerance
            return [
                credit
                for credit in state.records.bank_entries
                if credit.utr is None
                and bank_slot(credit.bank_entry_id) not in state.used_slots
                and int(credit.signed_amount_paise) == net
                and low <= credit.posted_at_utc <= high
            ]

        def candidate_settlements(credit: BankEntryRecord) -> list[SettlementRecord]:
            amount = int(credit.signed_amount_paise)
            return [
                settlement
                for settlement in state.records.settlements
                if settlement.utr is None
                and settlement_slot(settlement.settlement_id) not in state.used_slots
                and int(settlement.net_amount_paise) == amount
                and settlement.window_start_utc - tolerance
                <= credit.posted_at_utc
                <= settlement.window_end_utc + tolerance
            ]

        twin_groups: dict[frozenset[str], list[SettlementRecord]] = {}
        twin_credits: dict[frozenset[str], set[str]] = {}

        def record_ambiguity(settlement: SettlementRecord, credits: list[BankEntryRecord]) -> None:
            key = frozenset(credit.bank_entry_id for credit in credits)
            twin_groups.setdefault(key, []).append(settlement)
            twin_credits.setdefault(key, set()).update(credit.bank_entry_id for credit in credits)

        for settlement in state.records.settlements:
            if settlement.utr is not None:
                continue
            if settlement_slot(settlement.settlement_id) in state.used_slots:
                continue
            candidates = candidate_credits(settlement)
            if not candidates:
                state.missing_bank.append(MissingBankEvidence(settlement=settlement, has_utr=False))
                continue
            if len(candidates) == 1:
                credit = candidates[0]
                reverse = candidate_settlements(credit)
                if reverse == [settlement]:
                    state.used_slots.add(bank_slot(credit.bank_entry_id))
                    state.used_slots.add(settlement_slot(settlement.settlement_id))
                    state.matches.append(record_pair(settlement, credit, R_SETTLEMENT_BANK_UNIQUE))
                    continue
                record_ambiguity(settlement, candidates)
                continue
            record_ambiguity(settlement, candidates)

        for key in sorted(twin_groups, key=lambda item: sorted(item)):
            settlements = sorted(twin_groups[key], key=lambda item: item.settlement_id)
            state.twin_groups.append(
                TwinGroup(
                    settlement_ids=tuple(s.settlement_id for s in settlements),
                    bank_entry_ids=tuple(sorted(twin_credits[key])),
                )
            )
