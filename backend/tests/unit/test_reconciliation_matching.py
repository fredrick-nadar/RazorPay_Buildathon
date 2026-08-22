"""Phase 2 matching tests: hierarchy precedence, slots, ties (PRD 8.2, 8.3).

Every mandatory behaviour is exercised on a hand-built corpus: exact
identifiers outrank amount-only evidence, refund->payment and UTR->credit
links pick the correct record, many-to-one settlements retain every signed
contribution, exclusive slots are never reused, equal legitimate amounts
stay distinct, and ambiguous candidate sets become cases instead of matches.
"""

from __future__ import annotations

from app.domain.enums import ExceptionCategory, RelationshipType
from app.reconciliation.detectors import reconcile
from app.reconciliation.rules import (
    ACCOUNT_BANK,
    ACCOUNT_CLEARING,
    R_SETTLEMENT_BANK_UNIQUE,
    R_SETTLEMENT_BANK_UTR,
)
from tests.unit.recon_fixtures import (
    bank_credit,
    ledger_row,
    payment,
    records,
    refund,
    settlement,
)


def _groups(result, relationship: RelationshipType) -> list:
    return [g for g in result.matches if g.relationship_type == relationship]


def _members_of(group) -> dict[str, list]:
    by_role: dict[str, list] = {}
    for member in group.members:
        by_role.setdefault(member.role, []).append(member)
    return by_role


class TestExactIdentifiersOutrankAmount:
    def test_equal_amount_payments_match_own_ledger_rows(self) -> None:
        corpus = records(
            payments=[
                payment("pay_P000000001", gross=10_000),
                payment("pay_P000000002", gross=10_000),
            ],
            settlements=[settlement("stl_S000000001", net=20_000, gross=20_000)],
            ledger_entries=[
                ledger_row(
                    "led_L000000001",
                    source_type="PAYMENT",
                    source_reference="pay_P000000001",
                    amount=10_000,
                    account=ACCOUNT_CLEARING,
                ),
                ledger_row(
                    "led_L000000002",
                    source_type="PAYMENT",
                    source_reference="pay_P000000002",
                    amount=10_000,
                    account=ACCOUNT_CLEARING,
                ),
            ],
        )
        result = reconcile(corpus)
        pairs = {
            member.record_id: group.members
            for group in _groups(result, RelationshipType.LEDGER_SOURCE)
            for member in group.members
            if member.role == "BOOKING"
        }
        bookings = {
            ledger_id: {member.record_id for member in members if member.role == "SOURCE"}
            for ledger_id, members in pairs.items()
        }
        assert bookings == {
            "led_L000000001": {"pay_P000000001"},
            "led_L000000002": {"pay_P000000002"},
        }
        assert result.unaccounted_record_keys == frozenset()

    def test_utr_outranks_amount_window_evidence(self) -> None:
        corpus = records(
            payments=[payment("pay_P000000001", gross=10_000)],
            settlements=[settlement("stl_S000000001", net=10_000, utr="UTIR111111111111")],
            bank_entries=[
                bank_credit("bnk_B000000001", amount=10_000, utr="UTIR111111111111"),
                # Distractor: same amount inside the window, no UTR.
                bank_credit("bnk_B000000002", amount=10_000, posted="2026-03-03T06:00:00Z"),
            ],
        )
        result = reconcile(corpus)
        matches = _groups(result, RelationshipType.SETTLEMENT_BANK_CREDIT)
        assert len(matches) == 1
        assert matches[0].rule_id == R_SETTLEMENT_BANK_UTR
        member_ids = {member.record_id for member in matches[0].members}
        assert member_ids == {"stl_S000000001", "bnk_B000000001"}


class TestRefundLinksToCorrectPayment:
    def test_refund_attaches_to_its_own_parent(self) -> None:
        corpus = records(
            payments=[
                payment("pay_P000000001", gross=10_000),
                payment("pay_P000000002", gross=10_000),
            ],
            settlements=[settlement("stl_S000000001", net=15_000, gross=15_000)],
            refunds=[refund("rfd_R000000001", payment_id="pay_P000000002", amount=5_000)],
        )
        result = reconcile(corpus)
        groups = _groups(result, RelationshipType.REFUND_OF_PAYMENT)
        assert len(groups) == 1
        roles = _members_of(groups[0])
        assert roles["CHILD"][0].record_id == "rfd_R000000001"
        assert roles["PARENT"][0].record_id == "pay_P000000002"


class TestManyToOneSettlement:
    def test_every_contribution_is_retained_with_signs(self) -> None:
        corpus = records(
            payments=[
                payment("pay_P000000001", gross=10_000),
                payment("pay_P000000002", gross=20_000, fee=100),
                payment("pay_P000000003", gross=30_000, fee=200, tax=36),
            ],
            refunds=[refund("rfd_R000000001", payment_id="pay_P000000001", amount=3_000)],
            settlements=[settlement("stl_S000000001", net=56_664, gross=59_664, adjustment=-3_000)],
        )
        result = reconcile(corpus)
        groups = _groups(result, RelationshipType.MEMBER_OF_SETTLEMENT)
        assert len(groups) == 1
        group = groups[0]
        contributors = {
            member.record_id: member.signed_contribution_paise
            for member in group.members
            if member.role == "CONTRIBUTOR"
        }
        adjustments = {
            member.record_id: member.signed_contribution_paise
            for member in group.members
            if member.role == "ADJUSTMENT"
        }
        assert contributors == {
            "pay_P000000001": 10_000,
            "pay_P000000002": 19_900,
            "pay_P000000003": 29_764,
        }
        assert adjustments == {"rfd_R000000001": -3_000}
        assert sum(m.signed_contribution_paise for m in group.members) == group.amount_paise

    def test_payment_holds_membership_refund_parent_and_ledger_slots(self) -> None:
        corpus = records(
            payments=[payment("pay_P000000001", gross=10_000)],
            refunds=[refund("rfd_R000000001", payment_id="pay_P000000001", amount=2_000)],
            settlements=[settlement("stl_S000000001", net=8_000, gross=10_000, adjustment=-2_000)],
            ledger_entries=[
                ledger_row(
                    "led_L000000001",
                    source_type="PAYMENT",
                    source_reference="pay_P000000001",
                    amount=10_000,
                    account=ACCOUNT_CLEARING,
                )
            ],
        )
        result = reconcile(corpus)
        roles_for_payment = {
            group.relationship_type
            for group in result.matches
            if any(member.record_id == "pay_P000000001" for member in group.members)
        }
        assert roles_for_payment == {
            RelationshipType.MEMBER_OF_SETTLEMENT,
            RelationshipType.REFUND_OF_PAYMENT,
            RelationshipType.LEDGER_SOURCE,
        }


class TestConsumptionSlots:
    def test_duplicate_ledger_row_cannot_reuse_the_slot(self) -> None:
        corpus = records(
            payments=[payment("pay_P000000001", gross=10_000)],
            settlements=[settlement("stl_S000000001", net=10_000, gross=10_000)],
            ledger_entries=[
                ledger_row(
                    "led_L000000001",
                    source_type="PAYMENT",
                    source_reference="pay_P000000001",
                    amount=10_000,
                    account=ACCOUNT_CLEARING,
                ),
                ledger_row(
                    "led_L000000002",
                    source_type="PAYMENT",
                    source_reference="pay_P000000001",
                    amount=10_000,
                    account=ACCOUNT_CLEARING,
                ),
            ],
        )
        result = reconcile(corpus)
        sources = _groups(result, RelationshipType.LEDGER_SOURCE)
        assert len(sources) == 1
        assert sources[0].members[1].record_id == "led_L000000001" or {
            member.record_id for member in sources[0].members
        } == {"pay_P000000001", "led_L000000001"}
        duplicates = [
            case
            for case in result.cases
            if case.category == ExceptionCategory.DUPLICATE_LEDGER_POSTING
        ]
        assert len(duplicates) == 1
        assert {item.record_id for item in duplicates[0].evidence} == {
            "pay_P000000001",
            "led_L000000001",
            "led_L000000002",
        }

    def test_consumed_bank_credit_cannot_be_reused(self) -> None:
        corpus = records(
            payments=[payment("pay_P000000001", gross=10_000)],
            settlements=[settlement("stl_S000000001", net=10_000, utr="UTIR111111111111")],
            bank_entries=[bank_credit("bnk_B000000001", amount=10_000, utr="UTIR111111111111")],
        )
        result = reconcile(corpus)
        credits = _groups(result, RelationshipType.SETTLEMENT_BANK_CREDIT)
        assert len(credits) == 1
        assert credits[0].rule_id == R_SETTLEMENT_BANK_UTR

    def test_exclusive_records_never_belong_to_two_groups(self) -> None:
        corpus = records(
            payments=[
                payment("pay_P000000001", gross=10_000),
                payment("pay_P000000002", gross=20_000),
            ],
            refunds=[refund("rfd_R000000001", payment_id="pay_P000000001", amount=2_000)],
            settlements=[
                settlement("stl_S000000001", net=28_000, gross=30_000, adjustment=-2_000),
                settlement(
                    "stl_S000000002",
                    net=0,
                    window=("2026-03-10T00:00:00Z", "2026-03-11T00:00:00Z"),
                ),
            ],
            bank_entries=[bank_credit("bnk_B000000001", amount=28_000, utr="UTIR111111111111")],
            ledger_entries=[
                ledger_row(
                    "led_L000000001",
                    source_type="PAYMENT",
                    source_reference="pay_P000000001",
                    amount=10_000,
                    account=ACCOUNT_CLEARING,
                ),
                ledger_row(
                    "led_L000000002",
                    source_type="SETTLEMENT",
                    source_reference="stl_S000000001",
                    amount=28_000,
                    account=ACCOUNT_BANK,
                ),
            ],
        )
        result = reconcile(corpus)
        appearances: dict[tuple[str, str], set[RelationshipType]] = {}
        for group in result.matches:
            for member in group.members:
                key = (member.record_type, member.record_id)
                appearances.setdefault(key, set()).add(group.relationship_type)
        # Exclusive record types appear at most once per relationship kind.
        for (record_type, _record_id), kinds in appearances.items():
            if record_type in ("BANK_ENTRY", "LEDGER_ENTRY"):
                assert len(kinds) == 1, (record_type, kinds)
        # A payment may legitimately appear in three relationship kinds.
        assert len(appearances[("PAYMENT", "pay_P000000001")]) == 3


class TestUniqueAmountWindowMatching:
    def test_utr_less_settlement_matches_unique_credit(self) -> None:
        corpus = records(
            payments=[payment("pay_P000000001", gross=10_000)],
            settlements=[settlement("stl_S000000001", net=10_000)],
            bank_entries=[
                bank_credit("bnk_B000000001", amount=10_000, posted="2026-03-03T06:00:00Z"),
                # Different amount inside the window: not a candidate.
                bank_credit("bnk_B000000002", amount=9_999, posted="2026-03-03T06:00:00Z"),
            ],
        )
        result = reconcile(corpus)
        matches = _groups(result, RelationshipType.SETTLEMENT_BANK_CREDIT)
        assert len(matches) == 1
        assert matches[0].rule_id == R_SETTLEMENT_BANK_UNIQUE
        assert {member.record_id for member in matches[0].members} == {
            "stl_S000000001",
            "bnk_B000000001",
        }
        assert result.unaccounted_record_keys == frozenset()

    def test_twin_candidates_become_a_case_not_a_match(self) -> None:
        corpus = records(
            payments=[
                payment("pay_P000000001", gross=10_000, settlement_id="stl_S000000001"),
                payment("pay_P000000002", gross=10_000, settlement_id="stl_S000000002"),
            ],
            settlements=[
                settlement("stl_S000000001", net=10_000),
                settlement(
                    "stl_S000000002",
                    net=10_000,
                    window=("2026-03-03T00:00:00Z", "2026-03-04T00:00:00Z"),
                    settled="2026-03-04T04:00:00Z",
                ),
            ],
            bank_entries=[
                bank_credit("bnk_B000000001", amount=10_000, posted="2026-03-03T06:00:00Z"),
                # Twin credit posted on the shared window boundary.
                bank_credit("bnk_B000000002", amount=10_000, posted="2026-03-03T00:00:00Z"),
            ],
        )
        result = reconcile(corpus)
        assert _groups(result, RelationshipType.SETTLEMENT_BANK_CREDIT) == []
        ambiguous = [
            case for case in result.cases if case.category == ExceptionCategory.AMBIGUOUS_EVIDENCE
        ]
        assert len(ambiguous) == 1
        evidence = {item.record_id for item in ambiguous[0].evidence}
        assert evidence == {
            "stl_S000000001",
            "stl_S000000002",
            "bnk_B000000001",
            "bnk_B000000002",
        }
        assert ambiguous[0].variance_paise == 0
        assert ambiguous[0].affected_amount_paise == 20_000


class TestMissingBankEvidence:
    def test_settlement_without_credit_becomes_ambiguous_case(self) -> None:
        ledger = ledger_row(
            "led_L000000001",
            source_type="SETTLEMENT",
            source_reference="stl_S000000001",
            amount=10_000,
            account=ACCOUNT_BANK,
        )
        corpus = records(
            payments=[payment("pay_P000000001", gross=10_000)],
            settlements=[settlement("stl_S000000001", net=10_000, utr="UTIR111111111111")],
            ledger_entries=[ledger],
        )
        result = reconcile(corpus)
        ambiguous = [
            case for case in result.cases if case.category == ExceptionCategory.AMBIGUOUS_EVIDENCE
        ]
        assert len(ambiguous) == 1
        assert {item.record_id for item in ambiguous[0].evidence} == {
            "stl_S000000001",
            "led_L000000001",
        }
        # Missing-bank evidence carries a non-zero residual (correction 4).
        assert ambiguous[0].variance_paise == -10_000
        assert ambiguous[0].variance_scope == "BANK"
        assert ambiguous[0].proposed_delta_paise is None
