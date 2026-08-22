"""Exception injectors for the four frozen exception categories (PRD 4.2).

Each injector is a separate function that mutates the clean corpus and
returns ground-truth case records. Injectors operate on disjoint evidence
sets through the corpus reservation sets, so every injected case creates
exactly the intended labelled anomaly and clean records stay clean.

Labels record what was intentionally broken; ``control_totals.py`` re-derives
the same facts from the written files independently.
"""

from __future__ import annotations

from typing import Any

from app.evaluation.dataset_spec import GenerationSpec, shift_date
from app.evaluation.generator import Corpus, LedgerRec

CATEGORY_DUPLICATE = "DUPLICATE_LEDGER_POSTING"
CATEGORY_MISSING_REFUND = "MISSING_REFUND_POSTING"
CATEGORY_TIMING = "SETTLEMENT_TIMING_WINDOW_SHIFT"


def _ledger_rows_for(corpus: Corpus, reference: str, source_type: str) -> list[LedgerRec]:
    return [
        entry
        for entry in corpus.ledger_entries
        if entry.source_reference == reference and entry.source_type == source_type
    ]


def inject_duplicate_ledger_posting(corpus: Corpus, count: int) -> list[dict[str, Any]]:
    """Add a second identical posting for ``count`` reserved-clean payments."""
    cases: list[dict[str, Any]] = []
    if count == 0:
        return cases
    pool = sorted(
        p.payment_id for p in corpus.payments if p.payment_id not in corpus.reserved_payment_ids
    )
    for payment_id in corpus.rng.sample(pool, count):
        payment = corpus.payments_by_id()[payment_id]
        matches = _ledger_rows_for(corpus, payment_id, "PAYMENT")
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one ledger row for payment {payment_id}")
        original = matches[0]
        duplicate = LedgerRec(
            ledger_entry_id=corpus.new_id("led"),
            account_code=original.account_code,
            accounting_date=original.accounting_date,
            currency=original.currency,
            signed_paise=original.signed_paise,
            source_reference=original.source_reference,
            source_type=original.source_type,
            description=original.description,
            entry_origin=original.entry_origin,
        )
        position = corpus.ledger_entries.index(original)
        corpus.ledger_entries.insert(position + 1, duplicate)
        corpus.reserved_payment_ids.add(payment_id)
        cases.append(
            {
                "expected_category": CATEGORY_DUPLICATE,
                "expected_outcome": "APPROVAL_REQUIRED",
                "expected_evidence_ids": [
                    payment.payment_id,
                    original.ledger_entry_id,
                    duplicate.ledger_entry_id,
                ],
                "expected_delta_paise": -payment.net_paise,
                "must_escalate": False,
                "authoring_notes": (
                    "the same payment is posted twice with compatible amounts; "
                    "removing one duplicate restores the expected balance"
                ),
            }
        )
    return cases


def inject_missing_refund_posting(corpus: Corpus, count: int) -> list[dict[str, Any]]:
    """Remove the ledger posting of ``count`` processed refunds."""
    cases: list[dict[str, Any]] = []
    if count == 0:
        return cases
    pool = sorted(
        r.refund_id
        for r in corpus.refunds
        if r.refund_id not in corpus.reserved_refund_ids
        and r.payment_id not in corpus.reserved_payment_ids
    )
    for refund_id in corpus.rng.sample(pool, count):
        refund = corpus.refunds_by_id()[refund_id]
        matches = _ledger_rows_for(corpus, refund_id, "REFUND")
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one ledger row for refund {refund_id}")
        corpus.ledger_entries.remove(matches[0])
        corpus.reserved_refund_ids.add(refund.refund_id)
        corpus.reserved_payment_ids.add(refund.payment_id)
        corpus.reserved_settlement_ids.add(refund.settlement_id)
        cases.append(
            {
                "expected_category": CATEGORY_MISSING_REFUND,
                "expected_outcome": "APPROVAL_REQUIRED",
                "expected_evidence_ids": [
                    refund.refund_id,
                    refund.payment_id,
                    refund.settlement_id,
                ],
                "expected_delta_paise": -refund.refund_paise,
                "must_escalate": False,
                "authoring_notes": (
                    "a processed refund linked to a valid payment has no ledger "
                    "posting inside the posting window; adding the signed entry "
                    "restores the expected balance"
                ),
            }
        )
    return cases


def inject_settlement_timing_window_shift(corpus: Corpus, count: int) -> list[dict[str, Any]]:
    """Book ``count`` settlements into the adjacent accounting period."""
    cases: list[dict[str, Any]] = []
    if count == 0:
        return cases
    pool = sorted(
        s.settlement_id
        for s in corpus.settlements
        if not s.ambiguous and s.settlement_id not in corpus.reserved_settlement_ids
    )
    for settlement_id in corpus.rng.sample(pool, count):
        matches = _ledger_rows_for(corpus, settlement_id, "SETTLEMENT")
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one ledger row for settlement {settlement_id}")
        row = matches[0]
        row.accounting_date = shift_date(row.accounting_date, 1)
        corpus.reserved_settlement_ids.add(settlement_id)
        cases.append(
            {
                "expected_category": CATEGORY_TIMING,
                "expected_outcome": "VERIFIED_RESOLVED",
                "expected_evidence_ids": [settlement_id, row.ledger_entry_id],
                "expected_delta_paise": 0,
                "must_escalate": False,
                "authoring_notes": (
                    "the settlement is credited inside its window but booked into "
                    "the adjacent accounting period; period attribution only, total "
                    "economic value unchanged (zero-delta verified explanation)"
                ),
            }
        )
    return cases


def run_exception_injections(corpus: Corpus, spec: GenerationSpec) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    cases.extend(inject_duplicate_ledger_posting(corpus, spec.duplicate_cases))
    cases.extend(inject_missing_refund_posting(corpus, spec.missing_refund_cases))
    cases.extend(inject_settlement_timing_window_shift(corpus, spec.timing_shift_cases))
    cases.extend(corpus.ambiguity_cases)
    return cases
