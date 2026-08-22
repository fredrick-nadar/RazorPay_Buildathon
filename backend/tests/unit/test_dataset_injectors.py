"""Injector unit tests on small handcrafted corpora (PRD Phase 1).

Each injector must create exactly the intended anomaly, keep evidence sets
disjoint, and emit well-formed labels; the clean corpus must satisfy every
conservation identity before injection.
"""

from __future__ import annotations

import dataclasses

from app.domain.enums import ExceptionCategory
from app.evaluation.dataset_spec import GenerationSpec, epoch_seconds
from app.evaluation.generator import build_clean_corpus, corpus_rows
from app.evaluation.injectors import (
    inject_duplicate_ledger_posting,
    inject_missing_refund_posting,
    inject_settlement_timing_window_shift,
    run_exception_injections,
)

TINY_SPEC = GenerationSpec(
    profile="tiny",
    seed=1234,
    base_epoch_s=epoch_seconds(2026, 1, 5),
    window_count=4,
    ambiguous_pair_windows=(1,),
    payments_per_base_settlement=2,
    refund_count=2,
    duplicate_cases=1,
    missing_refund_cases=1,
    timing_shift_cases=1,
)


def corpus_totals(corpus) -> dict[str, int]:
    payment_net = sum(p.net_paise for p in corpus.payments)
    refund_total = sum(r.refund_paise for r in corpus.refunds)
    settlement_total = sum(s.net_paise for s in corpus.settlements)
    bank_total = sum(b.signed_paise for b in corpus.bank_entries)
    clearing = sum(
        e.signed_paise for e in corpus.ledger_entries if e.account_code.endswith("CLEARING")
    )
    bank_ledger = sum(
        e.signed_paise for e in corpus.ledger_entries if e.account_code.startswith("1100")
    )
    return {
        "payment_net": payment_net,
        "refund_total": refund_total,
        "settlement_total": settlement_total,
        "bank_total": bank_total,
        "clearing": clearing,
        "bank_ledger": bank_ledger,
    }


class TestCleanCorpus:
    def test_clean_identities_hold_before_injection(self) -> None:
        corpus = build_clean_corpus(TINY_SPEC)
        totals = corpus_totals(corpus)
        assert totals["settlement_total"] == totals["payment_net"] - totals["refund_total"]
        assert totals["bank_total"] == totals["settlement_total"]
        assert totals["clearing"] == totals["payment_net"] - totals["refund_total"]
        assert totals["bank_ledger"] == totals["settlement_total"]

    def test_twin_settlements_share_net_and_carry_no_utr(self) -> None:
        corpus = build_clean_corpus(TINY_SPEC)
        twins = [s for s in corpus.settlements if s.ambiguous]
        assert len(twins) == 2
        assert twins[0].net_paise == twins[1].net_paise
        assert twins[0].window_end == twins[1].window_start
        assert twins[0].utr == "" and twins[1].utr == ""
        twin_credits = [b for b in corpus.bank_entries if b.utr == ""]
        assert len(twin_credits) == 2
        boundary_credit = max(twin_credits, key=lambda b: b.posted_at)
        assert boundary_credit.posted_at == twins[1].window_start

    def test_settlement_nets_are_globally_unique_except_twins(self) -> None:
        corpus = build_clean_corpus(TINY_SPEC)
        nets = [s.net_paise for s in corpus.settlements]
        twin_nets = {s.net_paise for s in corpus.settlements if s.ambiguous}
        normal_nets = [s.net_paise for s in corpus.settlements if not s.ambiguous]
        assert len(set(normal_nets)) == len(normal_nets)
        assert len(twin_nets) == 1
        assert twin_nets.isdisjoint(normal_nets)
        assert len(nets) == len(normal_nets) + 2


class TestDuplicateInjector:
    def test_adds_exactly_n_identical_rows(self) -> None:
        corpus = build_clean_corpus(TINY_SPEC)
        before = len(corpus.ledger_entries)
        cases = inject_duplicate_ledger_posting(corpus, 1)
        assert len(cases) == 1
        assert len(corpus.ledger_entries) == before + 1
        case = cases[0]
        assert case["expected_category"] == ExceptionCategory.DUPLICATE_LEDGER_POSTING.value
        assert case["expected_outcome"] == "APPROVAL_REQUIRED"
        payment_id = next(e for e in case["expected_evidence_ids"] if e.startswith("pay_"))
        payment = corpus.payments_by_id()[payment_id]
        assert case["expected_delta_paise"] == -payment.net_paise
        rows = [
            e
            for e in corpus.ledger_entries
            if e.source_reference == payment_id and e.source_type == "PAYMENT"
        ]
        assert len(rows) == 2
        assert rows[0].signed_paise == rows[1].signed_paise == payment.net_paise


class TestMissingRefundInjector:
    def test_removes_exactly_n_refund_rows(self) -> None:
        corpus = build_clean_corpus(TINY_SPEC)
        before = len(corpus.ledger_entries)
        cases = inject_missing_refund_posting(corpus, 1)
        assert len(cases) == 1
        assert len(corpus.ledger_entries) == before - 1
        case = cases[0]
        assert case["expected_category"] == ExceptionCategory.MISSING_REFUND_POSTING.value
        refund_id = next(e for e in case["expected_evidence_ids"] if e.startswith("rfd_"))
        refund = corpus.refunds_by_id()[refund_id]
        assert case["expected_delta_paise"] == -refund.refund_paise
        rows = [
            e
            for e in corpus.ledger_entries
            if e.source_reference == refund_id and e.source_type == "REFUND"
        ]
        assert rows == []


class TestTimingInjector:
    def test_shifts_accounting_date_without_changing_amounts(self) -> None:
        corpus = build_clean_corpus(TINY_SPEC)
        before = corpus_totals(corpus)
        rows_before = len(corpus.ledger_entries)
        cases = inject_settlement_timing_window_shift(corpus, 1)
        assert len(cases) == 1
        assert len(corpus.ledger_entries) == rows_before
        after = corpus_totals(corpus)
        assert after == before, "timing shift must not change any amount"
        case = cases[0]
        assert case["expected_category"] == ExceptionCategory.SETTLEMENT_TIMING_WINDOW_SHIFT.value
        assert case["expected_delta_paise"] == 0
        assert case["expected_outcome"] == "VERIFIED_RESOLVED"
        settlement_id = next(e for e in case["expected_evidence_ids"] if e.startswith("stl_"))
        settlement = corpus.settlements_by_id()[settlement_id]
        entry = next(
            e
            for e in corpus.ledger_entries
            if e.source_reference == settlement_id and e.source_type == "SETTLEMENT"
        )
        from app.evaluation.dataset_spec import format_date

        assert entry.accounting_date not in (
            format_date(settlement.window_start),
            format_date(settlement.window_end),
        )


class TestInjectorDisjointness:
    def test_evidence_sets_are_disjoint(self) -> None:
        corpus = build_clean_corpus(TINY_SPEC)
        cases = run_exception_injections(corpus, TINY_SPEC)
        assert len(cases) == 4  # 1 duplicate + 1 missing + 1 timing + 1 ambiguity pair
        seen: set[str] = set()
        for case in cases:
            evidence = {str(e) for e in case["expected_evidence_ids"]}
            assert evidence.isdisjoint(seen), f"overlapping evidence: {evidence & seen}"
            seen |= evidence

    def test_categories_use_frozen_enum_values(self) -> None:
        corpus = build_clean_corpus(TINY_SPEC)
        cases = run_exception_injections(corpus, TINY_SPEC)
        valid = {c.value for c in ExceptionCategory}
        assert {case["expected_category"] for case in cases} <= valid

    def test_injection_counts_scale_with_spec(self) -> None:
        spec = dataclasses.replace(
            TINY_SPEC, duplicate_cases=2, missing_refund_cases=1, timing_shift_cases=0
        )
        corpus = build_clean_corpus(spec)
        cases = run_exception_injections(corpus, spec)
        by_category: dict[str, int] = {}
        for case in cases:
            by_category[case["expected_category"]] = (
                by_category.get(case["expected_category"], 0) + 1
            )
        assert by_category["DUPLICATE_LEDGER_POSTING"] == 2
        assert by_category["MISSING_REFUND_POSTING"] == 1
        assert "SETTLEMENT_TIMING_WINDOW_SHIFT" not in by_category
        assert by_category["AMBIGUOUS_EVIDENCE"] == 1

    def test_rows_serialize_deterministically(self) -> None:
        corpus_a = build_clean_corpus(TINY_SPEC)
        corpus_b = build_clean_corpus(TINY_SPEC)
        run_exception_injections(corpus_a, TINY_SPEC)
        run_exception_injections(corpus_b, TINY_SPEC)
        assert corpus_rows(corpus_a) == corpus_rows(corpus_b)
