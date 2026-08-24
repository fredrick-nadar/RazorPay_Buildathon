"""Deterministic clean-corpus generator and dev-profile orchestration.

The generator produces mathematically consistent synthetic financial records
for the ARGUS evaluation datasets. It is evaluator-side code: labels record
what the generator intentionally constructed or broke, and the checks in
``control_totals.py`` re-derive every conservation property from the written
files alone.

Determinism contract: one ``random.Random(seed)`` instance, sequential draws,
sorted iterations, no wall-clock, no environment, no locale. Money is signed
integer paise end to end; fee and tax use exact integer arithmetic and are
synthetic merchant policy values, not Razorpay policy.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from app.evaluation.dataset_spec import (
    DATASET_VERSION,
    LABEL_SCHEMA_VERSION,
    WINDOW_SECONDS,
    GenerationSpec,
    format_date,
    format_ts,
    shift_date,
)

ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
ID_LENGTH = 10

# Synthetic merchant policy (demo values, not Razorpay policy).
FEE_PERCENT = 2
TAX_PERCENT = 18

ACCOUNT_CLEARING = "2100-PAYMENTS-CLEARING"
ACCOUNT_BANK = "1100-BANK-OPERATING"
MERCHANT_NAME = "ARGUS DEMO MERCH"
BANK_ACCOUNT_FINGERPRINT = "FP-ARGUS-DEMO-01"


def paise_str(value: int) -> str:
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    return f"{sign}{absolute // 100}.{absolute % 100:02d}"


def fee_of(gross_paise: int) -> int:
    return (gross_paise * FEE_PERCENT + 50) // 100


def tax_of(fee_paise: int) -> int:
    return (fee_paise * TAX_PERCENT + 50) // 100


@dataclass
class PaymentRec:
    payment_id: str
    order_id: str
    status: str
    currency: str
    gross_paise: int
    fee_paise: int
    tax_paise: int
    captured_at: int
    settlement_id: str

    @property
    def net_paise(self) -> int:
        return self.gross_paise - self.fee_paise - self.tax_paise

    def to_row(self) -> dict[str, str]:
        return {
            "payment_id": self.payment_id,
            "order_id": self.order_id,
            "status": self.status,
            "currency": self.currency,
            "gross_amount": paise_str(self.gross_paise),
            "fee_amount": paise_str(self.fee_paise),
            "tax_amount": paise_str(self.tax_paise),
            "captured_at_utc": format_ts(self.captured_at),
            "settlement_id": self.settlement_id,
        }


@dataclass
class RefundRec:
    refund_id: str
    payment_id: str
    status: str
    currency: str
    refund_paise: int
    created_at: int
    settlement_id: str

    def to_row(self) -> dict[str, str]:
        return {
            "refund_id": self.refund_id,
            "payment_id": self.payment_id,
            "status": self.status,
            "currency": self.currency,
            "refund_amount": paise_str(self.refund_paise),
            "created_at_utc": format_ts(self.created_at),
            "settlement_id": self.settlement_id,
        }


@dataclass
class SettlementRec:
    settlement_id: str
    settled_at: int
    window_start: int
    window_end: int
    status: str
    currency: str
    gross_paise: int
    fee_paise: int
    tax_paise: int
    adjustment_paise: int
    net_paise: int
    utr: str
    ambiguous: bool
    member_payment_ids: list[str] = field(default_factory=list)
    refund_ids: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, str]:
        return {
            "settlement_id": self.settlement_id,
            "settled_at_utc": format_ts(self.settled_at),
            "window_start_utc": format_ts(self.window_start),
            "window_end_utc": format_ts(self.window_end),
            "status": self.status,
            "currency": self.currency,
            "gross_credit": paise_str(self.gross_paise),
            "fee_amount": paise_str(self.fee_paise),
            "tax_amount": paise_str(self.tax_paise),
            "adjustment_amount": paise_str(self.adjustment_paise),
            "net_amount": paise_str(self.net_paise),
            "utr": self.utr,
        }


@dataclass
class BankRec:
    bank_entry_id: str
    posted_at: int
    currency: str
    signed_paise: int
    narration: str
    utr: str
    account_fingerprint: str

    def to_row(self) -> dict[str, str]:
        return {
            "bank_entry_id": self.bank_entry_id,
            "posted_at_utc": format_ts(self.posted_at),
            "value_date": format_date(self.posted_at),
            "currency": self.currency,
            "signed_amount": paise_str(self.signed_paise),
            "narration": self.narration,
            "utr": self.utr,
            "account_fingerprint": self.account_fingerprint,
        }


@dataclass
class LedgerRec:
    ledger_entry_id: str
    account_code: str
    accounting_date: str
    currency: str
    signed_paise: int
    source_reference: str
    source_type: str
    description: str
    entry_origin: str

    def to_row(self) -> dict[str, str]:
        return {
            "ledger_entry_id": self.ledger_entry_id,
            "account_code": self.account_code,
            "accounting_date": self.accounting_date,
            "currency": self.currency,
            "signed_amount": paise_str(self.signed_paise),
            "source_reference": self.source_reference,
            "source_type": self.source_type,
            "description": self.description,
            "entry_origin": self.entry_origin,
        }


@dataclass
class Corpus:
    spec: GenerationSpec
    rng: random.Random
    payments: list[PaymentRec] = field(default_factory=list)
    refunds: list[RefundRec] = field(default_factory=list)
    settlements: list[SettlementRec] = field(default_factory=list)
    bank_entries: list[BankRec] = field(default_factory=list)
    ledger_entries: list[LedgerRec] = field(default_factory=list)
    # Ledger state immediately before exception injection (clean reference).
    clean_ledger: list[LedgerRec] = field(default_factory=list)
    ambiguity_cases: list[dict[str, Any]] = field(default_factory=list)
    reserved_payment_ids: set[str] = field(default_factory=set)
    reserved_refund_ids: set[str] = field(default_factory=set)
    reserved_settlement_ids: set[str] = field(default_factory=set)
    ambiguous_settlement_ids: set[str] = field(default_factory=set)
    twin_payment_ids: set[str] = field(default_factory=set)
    used_settlement_nets: set[int] = field(default_factory=set)
    _used_ids: set[str] = field(default_factory=set)

    def new_id(self, prefix: str) -> str:
        while True:
            suffix = "".join(self.rng.choices(ID_ALPHABET, k=ID_LENGTH))
            candidate = f"{prefix}_{suffix}"
            if candidate not in self._used_ids:
                self._used_ids.add(candidate)
                return candidate

    def new_utr(self) -> str:
        return f"UTIR{self.rng.randint(10**11, 10**12 - 1)}"

    def payments_by_id(self) -> dict[str, PaymentRec]:
        return {p.payment_id: p for p in self.payments}

    def settlements_by_id(self) -> dict[str, SettlementRec]:
        return {s.settlement_id: s for s in self.settlements}

    def refunds_by_id(self) -> dict[str, RefundRec]:
        return {r.refund_id: r for r in self.refunds}


@dataclass
class GenerationResult:
    spec: GenerationSpec
    rows: dict[str, list[dict[str, str]]]
    labels: dict[str, Any]
    label_metrics: dict[str, Any]
    columns: dict[str, tuple[str, ...]] | None = None


# ---------------------------------------------------------------------------
# Corpus construction helpers.
# ---------------------------------------------------------------------------


def add_payment(
    corpus: Corpus,
    captured_at: int,
    gross_paise: int,
    settlement: SettlementRec | None = None,
) -> PaymentRec:
    fee = fee_of(gross_paise)
    tax = tax_of(fee)
    payment = PaymentRec(
        payment_id=corpus.new_id("pay"),
        order_id=corpus.new_id("order"),
        status="CAPTURED",
        currency="INR",
        gross_paise=gross_paise,
        fee_paise=fee,
        tax_paise=tax,
        captured_at=captured_at,
        settlement_id=settlement.settlement_id if settlement else "",
    )
    corpus.payments.append(payment)
    if settlement is not None:
        settlement.member_payment_ids.append(payment.payment_id)
    return payment


def finalize_settlement(corpus: Corpus, settlement: SettlementRec) -> None:
    members = [p for p in corpus.payments if p.settlement_id == settlement.settlement_id]
    refunds = [r for r in corpus.refunds if r.settlement_id == settlement.settlement_id]
    settlement.member_payment_ids = [m.payment_id for m in members]
    settlement.refund_ids = [r.refund_id for r in refunds]
    settlement.gross_paise = sum(m.gross_paise for m in members)
    settlement.fee_paise = sum(m.fee_paise for m in members)
    settlement.tax_paise = sum(m.tax_paise for m in members)
    settlement.adjustment_paise = -sum(r.refund_paise for r in refunds)
    settlement.net_paise = (
        settlement.gross_paise
        - settlement.fee_paise
        - settlement.tax_paise
        + settlement.adjustment_paise
    )


def ensure_unique_net(corpus: Corpus, settlement: SettlementRec) -> None:
    finalize_settlement(corpus, settlement)
    guard = 0
    while settlement.net_paise in corpus.used_settlement_nets and guard < 1000:
        members = [p for p in corpus.payments if p.settlement_id == settlement.settlement_id]
        member = members[-1]
        member.gross_paise += 100
        member.fee_paise = fee_of(member.gross_paise)
        member.tax_paise = tax_of(member.fee_paise)
        finalize_settlement(corpus, settlement)
        guard += 1
    if settlement.net_paise in corpus.used_settlement_nets:
        raise RuntimeError(f"could not find a unique net for settlement {settlement.settlement_id}")
    corpus.used_settlement_nets.add(settlement.net_paise)


def build_base_settlement(corpus: Corpus, window_index: int) -> SettlementRec:
    spec = corpus.spec
    start, end = spec.window_bounds(window_index)
    settlement = SettlementRec(
        settlement_id=corpus.new_id("stl"),
        settled_at=end + corpus.rng.randint(3600, 21600),
        window_start=start,
        window_end=end,
        status="PROCESSED",
        currency="INR",
        gross_paise=0,
        fee_paise=0,
        tax_paise=0,
        adjustment_paise=0,
        net_paise=0,
        utr=corpus.new_utr(),
        ambiguous=False,
    )
    corpus.settlements.append(settlement)
    for _ in range(spec.payments_per_base_settlement):
        captured = start + corpus.rng.randint(600, WINDOW_SECONDS - 3600)
        gross = corpus.rng.randrange(10_000, 5_000_000 + 1, 100)
        add_payment(corpus, captured_at=captured, gross_paise=gross, settlement=settlement)
    return settlement


def build_ambiguity_pair(corpus: Corpus, window_index: int) -> None:
    """Twin settlements with identical nets in adjacent, UTR-less windows.

    The twin window's bank credit is posted exactly on the shared window
    boundary; both credits carry no UTR. Each credit therefore has exactly
    two valid settlement candidates under the documented evaluator rule.
    """
    spec = corpus.spec
    start1, end1 = spec.window_bounds(window_index)
    start2 = end1
    end2 = start2 + WINDOW_SECONDS
    grosses = [corpus.rng.randrange(10_000, 2_000_000 + 1, 100) for _ in range(2)]
    twins: list[SettlementRec] = []
    for window_start, window_end, settled_at in (
        (start1, end1, end1 - 7200),
        (start2, end2, start2),
    ):
        settlement = SettlementRec(
            settlement_id=corpus.new_id("stl"),
            settled_at=settled_at,
            window_start=window_start,
            window_end=window_end,
            status="PROCESSED",
            currency="INR",
            gross_paise=0,
            fee_paise=0,
            tax_paise=0,
            adjustment_paise=0,
            net_paise=0,
            utr="",
            ambiguous=True,
        )
        corpus.settlements.append(settlement)
        for gross in grosses:
            captured = window_start + corpus.rng.randint(600, WINDOW_SECONDS - 3600)
            payment = add_payment(
                corpus, captured_at=captured, gross_paise=gross, settlement=settlement
            )
            corpus.twin_payment_ids.add(payment.payment_id)
        finalize_settlement(corpus, settlement)
        twins.append(settlement)
    if twins[0].net_paise != twins[1].net_paise:
        raise RuntimeError("ambiguity twins must have identical nets")
    corpus.used_settlement_nets.add(twins[0].net_paise)
    for settlement in twins:
        corpus.ambiguous_settlement_ids.add(settlement.settlement_id)
        corpus.reserved_settlement_ids.add(settlement.settlement_id)
    for payment_id in twins[0].member_payment_ids + twins[1].member_payment_ids:
        corpus.reserved_payment_ids.add(payment_id)

    first, second = twins
    bank_first = BankRec(
        bank_entry_id=corpus.new_id("bnk"),
        posted_at=first.settled_at + 300,
        currency="INR",
        signed_paise=first.net_paise,
        narration=f"NEFT CR {MERCHANT_NAME} SETTLEMENT {first.settlement_id}",
        utr="",
        account_fingerprint=BANK_ACCOUNT_FINGERPRINT,
    )
    bank_second = BankRec(
        bank_entry_id=corpus.new_id("bnk"),
        posted_at=start2,
        currency="INR",
        signed_paise=second.net_paise,
        narration=f"NEFT CR {MERCHANT_NAME} SETTLEMENT {second.settlement_id}",
        utr="",
        account_fingerprint=BANK_ACCOUNT_FINGERPRINT,
    )
    corpus.bank_entries.extend((bank_first, bank_second))
    corpus.ambiguity_cases.append(
        {
            "expected_category": "AMBIGUOUS_EVIDENCE",
            "expected_outcome": "UNRESOLVED",
            "expected_evidence_ids": [
                first.settlement_id,
                second.settlement_id,
                bank_first.bank_entry_id,
                bank_second.bank_entry_id,
            ],
            "expected_delta_paise": None,
            "must_escalate": True,
            "authoring_notes": (
                "twin settlements with identical net in adjacent windows; bank "
                "credits carry no UTR and the twin credit is posted exactly on the "
                "shared window boundary; the missing UTR is the absent discriminator"
            ),
        }
    )


def add_refunds(corpus: Corpus, count: int) -> None:
    if count == 0:
        return
    settlements_by_id = corpus.settlements_by_id()
    base_ids = sorted(
        p.payment_id
        for p in corpus.payments
        if p.settlement_id and p.settlement_id not in corpus.ambiguous_settlement_ids
    )
    chosen = corpus.rng.sample(base_ids, count)
    payments_by_id = corpus.payments_by_id()
    for payment_id in chosen:
        payment = payments_by_id[payment_id]
        settlement = settlements_by_id[payment.settlement_id]
        pct = corpus.rng.randrange(20, 81)
        amount = max(100, (payment.gross_paise * pct // 100) // 100 * 100)
        created = min(
            payment.captured_at + corpus.rng.randint(1800, 40000),
            settlement.window_end - 60,
        )
        refund = RefundRec(
            refund_id=corpus.new_id("rfd"),
            payment_id=payment.payment_id,
            status="PROCESSED",
            currency="INR",
            refund_paise=amount,
            created_at=created,
            settlement_id=settlement.settlement_id,
        )
        corpus.refunds.append(refund)
    for settlement in corpus.settlements:
        if not settlement.ambiguous:
            ensure_unique_net(corpus, settlement)


def build_bank_entries(corpus: Corpus) -> None:
    for settlement in corpus.settlements:
        if settlement.ambiguous:
            continue
        posted = settlement.settled_at + 300
        corpus.bank_entries.append(
            BankRec(
                bank_entry_id=corpus.new_id("bnk"),
                posted_at=posted,
                currency="INR",
                signed_paise=settlement.net_paise,
                narration=(
                    f"NEFT CR {settlement.utr} {MERCHANT_NAME} "
                    f"SETTLEMENT {settlement.settlement_id}"
                ),
                utr=settlement.utr,
                account_fingerprint=BANK_ACCOUNT_FINGERPRINT,
            )
        )


def build_ledger_entries(corpus: Corpus) -> None:
    for payment in corpus.payments:
        corpus.ledger_entries.append(
            LedgerRec(
                ledger_entry_id=corpus.new_id("led"),
                account_code=ACCOUNT_CLEARING,
                accounting_date=format_date(payment.captured_at),
                currency="INR",
                signed_paise=payment.net_paise,
                source_reference=payment.payment_id,
                source_type="PAYMENT",
                description=f"Payment captured {payment.payment_id}",
                entry_origin="IMPORTED",
            )
        )
    for refund in corpus.refunds:
        corpus.ledger_entries.append(
            LedgerRec(
                ledger_entry_id=corpus.new_id("led"),
                account_code=ACCOUNT_CLEARING,
                accounting_date=format_date(refund.created_at),
                currency="INR",
                signed_paise=-refund.refund_paise,
                source_reference=refund.refund_id,
                source_type="REFUND",
                description=f"Refund processed {refund.refund_id}",
                entry_origin="IMPORTED",
            )
        )
    for settlement in corpus.settlements:
        corpus.ledger_entries.append(
            LedgerRec(
                ledger_entry_id=corpus.new_id("led"),
                account_code=ACCOUNT_BANK,
                accounting_date=format_date(settlement.settled_at),
                currency="INR",
                signed_paise=settlement.net_paise,
                source_reference=settlement.settlement_id,
                source_type="SETTLEMENT",
                description=f"Settlement credited {settlement.settlement_id}",
                entry_origin="IMPORTED",
            )
        )


def _assert_clean_conservation(corpus: Corpus) -> None:
    payment_net_total = sum(p.net_paise for p in corpus.payments)
    refund_total = sum(r.refund_paise for r in corpus.refunds)
    settlement_total = sum(s.net_paise for s in corpus.settlements)
    bank_total = sum(b.signed_paise for b in corpus.bank_entries)
    if settlement_total != payment_net_total - refund_total:
        raise RuntimeError(
            f"clean corpus identity broken: settlements {settlement_total} != "
            f"payments {payment_net_total} - refunds {refund_total}"
        )
    if bank_total != settlement_total:
        raise RuntimeError(
            f"clean corpus identity broken: bank {bank_total} != settlements {settlement_total}"
        )
    ledger_clearing = sum(
        r.signed_paise for r in corpus.ledger_entries if r.account_code == ACCOUNT_CLEARING
    )
    ledger_bank = sum(
        r.signed_paise for r in corpus.ledger_entries if r.account_code == ACCOUNT_BANK
    )
    if ledger_clearing != payment_net_total - refund_total:
        raise RuntimeError("clean corpus identity broken: ledger clearing total")
    if ledger_bank != settlement_total:
        raise RuntimeError("clean corpus identity broken: ledger bank total")


def build_clean_corpus(spec: GenerationSpec) -> Corpus:
    corpus = Corpus(spec=spec, rng=random.Random(spec.seed))
    pair_windows: set[int] = set()
    for window_index in spec.ambiguous_pair_windows:
        pair_windows.add(window_index)
        pair_windows.add(window_index + 1)
        build_ambiguity_pair(corpus, window_index)
    for window_index in range(spec.window_count):
        if window_index in pair_windows:
            continue
        build_base_settlement(corpus, window_index)
    add_refunds(corpus, spec.refund_count)
    build_bank_entries(corpus)
    build_ledger_entries(corpus)
    corpus.clean_ledger = list(corpus.ledger_entries)
    _assert_clean_conservation(corpus)
    return corpus


# ---------------------------------------------------------------------------
# Serialization to final file rows.
# ---------------------------------------------------------------------------


def corpus_rows(corpus: Corpus) -> dict[str, list[dict[str, str]]]:
    payments = sorted(
        (p.to_row() for p in corpus.payments),
        key=lambda row: (row["captured_at_utc"], row["payment_id"]),
    )
    refunds = sorted(
        (r.to_row() for r in corpus.refunds),
        key=lambda row: (row["created_at_utc"], row["refund_id"]),
    )
    settlements = sorted(
        (s.to_row() for s in corpus.settlements),
        key=lambda row: (row["settled_at_utc"], row["settlement_id"]),
    )
    bank_entries = sorted(
        (b.to_row() for b in corpus.bank_entries),
        key=lambda row: (row["posted_at_utc"], row["bank_entry_id"]),
    )
    ledger_entries = sorted(
        (entry.to_row() for entry in corpus.ledger_entries),
        key=lambda row: (row["accounting_date"], row["ledger_entry_id"]),
    )
    return {
        "payments": payments,
        "refunds": refunds,
        "settlements": settlements,
        "bank_entries": bank_entries,
        "ledger_entries": ledger_entries,
    }


def assemble_labels(
    spec: GenerationSpec,
    corpus: Corpus,
    cases: list[dict[str, Any]],
    row_expectations: list[dict[str, Any]],
) -> dict[str, Any]:
    numbered: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        numbered.append({**case, "case_id": f"case-{spec.profile}-{index:04d}"})
    by_category: dict[str, int] = {}
    for case in numbered:
        category = str(case["expected_category"])
        by_category[category] = by_category.get(category, 0) + 1
    ledger_total = sum(entry.signed_paise for entry in corpus.clean_ledger)
    by_account: dict[str, int] = {}
    for entry in corpus.clean_ledger:
        by_account[entry.account_code] = by_account.get(entry.account_code, 0) + entry.signed_paise
    return {
        "dataset_version": DATASET_VERSION,
        "label_schema_version": LABEL_SCHEMA_VERSION,
        "profile": spec.profile,
        "seed": spec.seed,
        "summary": {
            "case_count": len(numbered),
            "by_category": dict(sorted(by_category.items())),
            "row_expectation_count": len(row_expectations),
        },
        "cases": numbered,
        "row_expectations": row_expectations,
        "clean_reference": {
            "ledger_total_paise": ledger_total,
            "ledger_by_account_paise": dict(sorted(by_account.items())),
            "derivation": "ledger state immediately before exception injection",
        },
    }


def generate_dataset(spec: GenerationSpec) -> GenerationResult:
    from app.evaluation import control_totals as ct
    from app.evaluation.injectors import run_exception_injections

    if spec.adversarial:
        from app.evaluation.adversarial import generate_adversarial

        return generate_adversarial(spec)

    corpus = build_clean_corpus(spec)
    cases = run_exception_injections(corpus, spec)
    labels = assemble_labels(spec, corpus, cases, row_expectations=[])
    rows = corpus_rows(corpus)

    # Label metrics are derived from canonical rows before any serialization
    # variation is applied (the variation is byte-shape only).
    ds = ct.rows_to_dataset_rows(rows, labels)
    metrics = ct.eligible_metrics(ds)

    columns: dict[str, tuple[str, ...]] | None = None
    if spec.profile == "holdout":
        # PRD 13.3: the frozen holdout must not share the dev dataset's byte
        # shape. The variation transform is an independent module (see
        # holdout_variation.py) and preserves economics exactly; labels on
        # this path carry no row-position semantics.
        from app.evaluation.dataset_spec import COLUMNS
        from app.evaluation.holdout_variation import apply_holdout_variation

        variation = apply_holdout_variation(spec.seed, rows, COLUMNS)
        rows = variation.rows
        columns = variation.columns

    return GenerationResult(
        spec=spec, rows=rows, labels=labels, label_metrics=metrics, columns=columns
    )


__all__ = [
    "ACCOUNT_BANK",
    "ACCOUNT_CLEARING",
    "BANK_ACCOUNT_FINGERPRINT",
    "BankRec",
    "Corpus",
    "GenerationResult",
    "LedgerRec",
    "MERCHANT_NAME",
    "PaymentRec",
    "RefundRec",
    "SettlementRec",
    "add_payment",
    "assemble_labels",
    "build_ambiguity_pair",
    "build_base_settlement",
    "build_bank_entries",
    "build_clean_corpus",
    "build_ledger_entries",
    "corpus_rows",
    "fee_of",
    "finalize_settlement",
    "ensure_unique_net",
    "generate_dataset",
    "paise_str",
    "tax_of",
    "shift_date",
]
