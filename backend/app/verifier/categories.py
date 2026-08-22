"""Category verifiers (PRD 9.3-9.6).

Each verifier receives a case, its structured hypothesis, and the evidence
snapshot, and returns a :class:`~app.verifier.models.VerifierResult` built
entirely from record arithmetic — never from hypothesis prose. Every check is
recorded as an :class:`~app.verifier.models.Equation` with concrete integer
paise values, so a proof package shows exactly which arithmetic held.

The ambiguity verifier has a single return path to ``INCONCLUSIVE``: no code
path in this module can turn ambiguous evidence into a ``PASS`` or a proposed
delta (PRD 9.6, Phase 3 stop condition).
"""

from __future__ import annotations

from datetime import timedelta

from app.domain.enums import ExceptionCategory, ReasonCode, VerifierStatus
from app.domain.records import LedgerEntryRecord
from app.reconciliation.detectors import CaseRecord
from app.reconciliation.engine import composition_candidates
from app.reconciliation.rules import (
    ACCOUNT_BANK,
    ACCOUNT_CLEARING,
    REFUND_POSTING_WINDOW_DAYS,
)
from app.verifier.models import (
    Equation,
    RejectedAlternative,
    StructuredHypothesis,
    VerifierResult,
    parse_evidence_id,
)
from app.verifier.rules import (
    REFUND_STATUS_PROCESSED,
    TIMING_ADJACENCY_DAYS,
    V_AMBIGUITY,
    V_DUPLICATE_LEDGER,
    V_MISSING_REFUND,
    V_TIMING_WINDOW,
    VERIFIER_RULE_VERSIONS,
)
from app.verifier.snapshot import EvidenceSnapshot


class _Checks:
    """Equation accumulator; any failed check records its stable reason code."""

    def __init__(self) -> None:
        self.equations: list[Equation] = []
        self.failures: list[str] = []

    def check(self, label: str, expression: str, holds: bool, code: str | None = None) -> bool:
        self.equations.append(Equation(label, expression, holds))
        if not holds and code is not None:
            self.failures.append(code)
        return holds

    def failed(self) -> bool:
        return bool(self.failures)


def _result(
    category: ExceptionCategory,
    rule_id: str,
    checks: _Checks,
    *,
    supported: list[str],
    conflicting: list[str],
    proposed_delta: int | None = None,
    rejected: tuple[RejectedAlternative, ...] = (),
    uncertainty: tuple[str, ...] = (),
    competing: tuple[str, ...] = (),
    missing_discriminator: str | None = None,
    next_step: str | None = None,
    inconclusive: bool = False,
    extra_codes: tuple[str, ...] = (),
) -> VerifierResult:
    if inconclusive:
        status = VerifierStatus.INCONCLUSIVE
    elif checks.failed():
        status = VerifierStatus.FAIL
    else:
        status = VerifierStatus.PASS
    codes = set(checks.failures) | set(extra_codes)
    if status == VerifierStatus.INCONCLUSIVE:
        codes.add(ReasonCode.NON_UNIQUE_EVIDENCE.value)
    return VerifierResult(
        status=status,
        category=category,
        rule_id=rule_id,
        rule_version=VERIFIER_RULE_VERSIONS[rule_id],
        reason_codes=tuple(sorted(codes)),
        equations=tuple(checks.equations),
        supported_evidence_ids=tuple(sorted(set(supported))),
        conflicting_evidence_ids=tuple(sorted(set(conflicting))),
        proposed_delta_paise=proposed_delta if status == VerifierStatus.PASS else None,
        rejected_alternatives=rejected if status == VerifierStatus.PASS else (),
        uncertainty=uncertainty,
        competing_candidates=competing,
        missing_discriminator=missing_discriminator,
        recommended_next_step=next_step,
    )


def _split_evidence(
    hypothesis: StructuredHypothesis,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Cited evidence split into (source-side, ledger-row) items."""
    source_items: list[tuple[str, str]] = []
    ledger_items: list[tuple[str, str]] = []
    for evidence_id in hypothesis.evidence_ids:
        parsed = parse_evidence_id(evidence_id)
        if parsed is None:
            continue
        record_type, record_id = parsed
        if record_type == "LEDGER_ENTRY":
            ledger_items.append((record_type, record_id))
        else:
            source_items.append((record_type, record_id))
    return source_items, ledger_items


# ---------------------------------------------------------------------------
# PRD 9.3 — duplicate ledger posting.
# ---------------------------------------------------------------------------


def _duplicate_source_semantics_holds(
    source_type: str,
    source_id: str,
    account: str,
    signed: int,
    snapshot: EvidenceSnapshot,
) -> bool:
    """Exactly one economic event on the source side explains the posting."""
    if source_type == "PAYMENT":
        payment = snapshot.payments.get(source_id)
        if payment is None or account != ACCOUNT_CLEARING:
            return False
        if signed == int(payment.net_paise):
            return True
        if signed < 0:
            refunds = [item for item in snapshot.refunds.values() if item.payment_id == source_id]
            candidates = composition_candidates(refunds, -signed)
            return len(candidates) == 1
        return False
    if source_type == "REFUND":
        refund = snapshot.refunds.get(source_id)
        return (
            refund is not None
            and account == ACCOUNT_CLEARING
            and signed == -int(refund.refund_amount_paise)
        )
    if source_type == "SETTLEMENT":
        settlement = snapshot.settlements.get(source_id)
        return (
            settlement is not None
            and account == ACCOUNT_BANK
            and signed == int(settlement.net_amount_paise)
        )
    return False


def verify_duplicate_ledger(
    case: CaseRecord, hypothesis: StructuredHypothesis, snapshot: EvidenceSnapshot
) -> VerifierResult:
    rule_id = V_DUPLICATE_LEDGER
    checks = _Checks()
    source_items, ledger_items = _split_evidence(hypothesis)

    shape_ok = len(source_items) == 1 and len(ledger_items) >= 2
    checks.check(
        "evidence-shape",
        f"cited evidence = 1 source record + {len(ledger_items)} ledger rows (>= 2 required)",
        shape_ok,
        ReasonCode.MISSING_EVIDENCE.value,
    )
    if not shape_ok:
        return _result(
            case.category,
            rule_id,
            checks,
            supported=list(hypothesis.evidence_ids),
            conflicting=[],
        )

    source_type, source_id = source_items[0]
    rows: list[LedgerEntryRecord] = []
    for _type, record_id in ledger_items:
        found = snapshot.ledger_entries.get(record_id)
        if found is None:
            checks.check(
                "row-exists",
                f"ledger row {record_id} exists in snapshot",
                False,
                ReasonCode.UNKNOWN_EVIDENCE_ID.value,
            )
            return _result(
                case.category,
                rule_id,
                checks,
                supported=list(hypothesis.evidence_ids),
                conflicting=[],
            )
        rows.append(found)
    rows.sort(key=lambda row: row.ledger_entry_id)

    cite_ok = all(
        row.source_type == source_type and row.source_reference == source_id for row in rows
    )
    checks.check(
        "rows-cite-one-source",
        f"all {len(rows)} rows cite source {source_type}:{source_id}",
        cite_ok,
        ReasonCode.REFERENCE_CONFLICT.value,
    )
    if not cite_ok:
        return _result(
            case.category,
            rule_id,
            checks,
            supported=[f"{source_type}:{source_id}"],
            conflicting=[f"LEDGER_ENTRY:{row.ledger_entry_id}" for row in rows],
        )

    signed_values = [int(row.signed_amount_paise) for row in rows]
    accounts = {row.account_code for row in rows}
    currencies = {row.currency for row in rows}
    identical = (
        len(set(signed_values)) == 1
        and len(accounts) == 1
        and len(currencies) == 1
        and all(value == signed_values[0] for value in signed_values)
    )
    conflicting_rows = [
        f"LEDGER_ENTRY:{row.ledger_entry_id}"
        for row, value in zip(rows, signed_values, strict=True)
        if value != signed_values[0]
    ]
    checks.check(
        "identical-postings",
        f"signed amounts {sorted(set(signed_values))} and accounts {sorted(accounts)} identical",
        identical,
        ReasonCode.AMOUNT_MISMATCH.value,
    )

    signed = signed_values[0]
    account = sorted(accounts)[0]
    semantics_ok = _duplicate_source_semantics_holds(
        source_type, source_id, account, signed, snapshot
    )
    checks.check(
        "single-source-event",
        f"one {source_type} event {source_id} explains a posting of {signed} on {account}",
        semantics_ok,
        ReasonCode.AMOUNT_MISMATCH.value,
    )

    snapshot_rows = snapshot.ledger_rows_by_source.get((source_type, source_id), ())
    cited_ids = {row.ledger_entry_id for row in rows}
    uncited = [
        f"LEDGER_ENTRY:{row.ledger_entry_id}"
        for row in snapshot_rows
        if row.ledger_entry_id not in cited_ids
    ]
    complete = not uncited
    checks.check(
        "evidence-complete",
        f"snapshot holds {len(snapshot_rows)} rows for {source_id}; all cited",
        complete,
        ReasonCode.MISSING_EVIDENCE.value,
    )

    count = len(rows)
    expected_variance = (count - 1) * signed
    variance_ok = case.variance_paise == expected_variance
    checks.check(
        "variance-consistency",
        f"case.variance({case.variance_paise}) == (rows-1)*signed == {expected_variance}",
        variance_ok,
        ReasonCode.AMOUNT_MISMATCH.value,
    )

    delta = -(count - 1) * signed
    ledger_sum = sum(signed_values)
    checks.check(
        "delta-restores-ledger",
        f"ledger_sum({ledger_sum}) + delta({delta}) == single expected posting({signed})",
        ledger_sum + delta == signed,
        ReasonCode.CONTROL_TOTAL_VIOLATION.value,
    )
    checks.check(
        "delta-closes-variance",
        f"variance({case.variance_paise}) + delta({delta}) == 0",
        case.variance_paise + delta == 0,
        ReasonCode.INVALID_PROPOSED_DELTA.value,
    )

    if checks.failed():
        return _result(
            case.category,
            rule_id,
            checks,
            supported=[
                f"{source_type}:{source_id}",
                *[f"LEDGER_ENTRY:{r.ledger_entry_id}" for r in rows],
            ],
            conflicting=conflicting_rows or uncited,
        )

    return _result(
        case.category,
        rule_id,
        checks,
        supported=[
            f"{source_type}:{source_id}",
            *[f"LEDGER_ENTRY:{r.ledger_entry_id}" for r in rows],
        ],
        conflicting=[],
        proposed_delta=delta,
        rejected=(
            RejectedAlternative(
                description="two legitimate equal-value source events",
                reason_codes=(ReasonCode.REFERENCE_CONFLICT.value,),
                evidence_ids=(f"{source_type}:{source_id}",),
            ),
            RejectedAlternative(
                description="intentional reversal pair offsetting the original",
                reason_codes=(ReasonCode.AMOUNT_MISMATCH.value,),
                evidence_ids=(f"LEDGER_ENTRY:{rows[0].ledger_entry_id}",),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# PRD 9.4 — missing refund posting.
# ---------------------------------------------------------------------------


def verify_missing_refund(
    case: CaseRecord, hypothesis: StructuredHypothesis, snapshot: EvidenceSnapshot
) -> VerifierResult:
    rule_id = V_MISSING_REFUND
    checks = _Checks()
    source_items, ledger_items = _split_evidence(hypothesis)
    refund_items = [item for item in source_items if item[0] == "REFUND"]
    payment_items = [item for item in source_items if item[0] == "PAYMENT"]
    settlement_items = [item for item in source_items if item[0] == "SETTLEMENT"]

    shape_ok = len(refund_items) == 1 and len(payment_items) == 1 and not ledger_items
    checks.check(
        "evidence-shape",
        f"cited evidence = refund {refund_items} + payment {payment_items}, no ledger rows",
        shape_ok,
        ReasonCode.MISSING_EVIDENCE.value,
    )
    if not shape_ok:
        return _result(
            case.category,
            rule_id,
            checks,
            supported=list(hypothesis.evidence_ids),
            conflicting=[],
        )

    refund = snapshot.refunds.get(refund_items[0][1])
    payment_id = payment_items[0][1]
    if refund is None:
        checks.check(
            "refund-exists",
            f"refund {refund_items[0][1]} exists in snapshot",
            False,
            ReasonCode.UNKNOWN_EVIDENCE_ID.value,
        )
        return _result(
            case.category, rule_id, checks, supported=list(hypothesis.evidence_ids), conflicting=[]
        )

    eligible = refund.status == REFUND_STATUS_PROCESSED
    checks.check(
        "refund-eligible",
        f"refund status {refund.status!r} == {REFUND_STATUS_PROCESSED!r}",
        eligible,
        ReasonCode.MISSING_EVIDENCE.value,
    )

    parent_ok = refund.payment_id == payment_id
    checks.check(
        "parent-valid",
        f"refund.payment_id({refund.payment_id}) == cited payment({payment_id})",
        parent_ok,
        ReasonCode.REFERENCE_CONFLICT.value,
    )

    settlement_consistent = True
    if settlement_items:
        settlement_consistent = settlement_items[0][1] == refund.settlement_id
    checks.check(
        "settlement-context",
        f"cited settlement {settlement_items} == refund.settlement_id({refund.settlement_id})",
        settlement_consistent,
        ReasonCode.REFERENCE_CONFLICT.value,
    )
    if refund.settlement_id is not None and refund.settlement_id not in snapshot.settlements:
        checks.check(
            "settlement-resolvable",
            f"settlement {refund.settlement_id} exists in snapshot",
            False,
            ReasonCode.MISSING_EVIDENCE.value,
        )

    citing_rows = list(snapshot.ledger_rows_by_source.get(("REFUND", refund.refund_id), ()))
    window_end = (refund.created_at_utc + timedelta(days=REFUND_POSTING_WINDOW_DAYS)).date()
    in_window = [row for row in citing_rows if row.accounting_date <= window_end]
    posting_absent = refund.refund_id not in snapshot.posted_refund_ids and not citing_rows
    checks.check(
        "posting-absent",
        f"no ledger row cites refund {refund.refund_id} within [{refund.created_at_utc.date()},"
        f" {window_end}] and no composition covers it",
        posting_absent,
        ReasonCode.REFERENCE_CONFLICT.value
        if in_window
        else ReasonCode.OUTSIDE_ALLOWED_WINDOW.value,
    )
    composition_ambiguous = refund.refund_id in snapshot.ambiguous_refund_ids
    checks.check(
        "composition-unique",
        f"refund {refund.refund_id} not part of a non-unique aggregate composition",
        not composition_ambiguous,
        ReasonCode.NON_UNIQUE_EVIDENCE.value,
    )
    if composition_ambiguous:
        return _result(
            case.category,
            rule_id,
            checks,
            supported=list(hypothesis.evidence_ids),
            conflicting=[],
            inconclusive=True,
            competing=tuple(sorted(item for item in hypothesis.evidence_ids)),
            missing_discriminator="per-refund posting references for the aggregate deduction rows",
            next_step="obtain the per-refund posting breakdown from the ledger owner",
        )

    amount = int(refund.refund_amount_paise)
    variance_ok = case.variance_paise == amount
    checks.check(
        "variance-consistency",
        f"case.variance({case.variance_paise}) == refund_amount({amount})",
        variance_ok,
        ReasonCode.AMOUNT_MISMATCH.value,
    )

    delta = -amount
    checks.check(
        "delta-closes-variance",
        f"variance({case.variance_paise}) + delta({delta}) == 0",
        case.variance_paise + delta == 0,
        ReasonCode.INVALID_PROPOSED_DELTA.value,
    )

    if checks.failed():
        return _result(
            case.category,
            rule_id,
            checks,
            supported=[f"REFUND:{refund.refund_id}", f"PAYMENT:{payment_id}"],
            conflicting=[f"LEDGER_ENTRY:{row.ledger_entry_id}" for row in citing_rows],
        )

    supported = [f"REFUND:{refund.refund_id}", f"PAYMENT:{payment_id}"]
    if refund.settlement_id is not None and refund.settlement_id in snapshot.settlements:
        supported.append(f"SETTLEMENT:{refund.settlement_id}")
    return _result(
        case.category,
        rule_id,
        checks,
        supported=supported,
        conflicting=[],
        proposed_delta=delta,
        rejected=(
            RejectedAlternative(
                description="posting exists under a different reference",
                reason_codes=(ReasonCode.REFERENCE_CONFLICT.value,),
                evidence_ids=(f"REFUND:{refund.refund_id}",),
            ),
            RejectedAlternative(
                description="refund posted in a later accounting window",
                reason_codes=(ReasonCode.OUTSIDE_ALLOWED_WINDOW.value,),
                evidence_ids=(f"REFUND:{refund.refund_id}",),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# PRD 9.5 — settlement timing-window shift.
# ---------------------------------------------------------------------------


def _booking_shifted(settlement_id: str, snapshot: EvidenceSnapshot) -> LedgerEntryRecord | None:
    """The settlement's bank-ledger booking when it sits outside its window."""
    rows = snapshot.ledger_rows_by_source.get(("SETTLEMENT", settlement_id), ())
    settlement = snapshot.settlements.get(settlement_id)
    if settlement is None:
        return None
    for row in rows:
        if (
            row.account_code == ACCOUNT_BANK
            and int(row.signed_amount_paise) == int(settlement.net_amount_paise)
            and not (
                settlement.window_start_date <= row.accounting_date <= settlement.window_end_date
            )
        ):
            return row
    return None


def verify_timing_window(
    case: CaseRecord, hypothesis: StructuredHypothesis, snapshot: EvidenceSnapshot
) -> VerifierResult:
    rule_id = V_TIMING_WINDOW
    checks = _Checks()
    source_items, ledger_items = _split_evidence(hypothesis)
    settlement_items = [item for item in source_items if item[0] == "SETTLEMENT"]

    shape_ok = len(settlement_items) == 1 and len(ledger_items) == 1
    checks.check(
        "evidence-shape",
        f"cited evidence = settlement {settlement_items} + one ledger row {ledger_items}",
        shape_ok,
        ReasonCode.MISSING_EVIDENCE.value,
    )
    if not shape_ok:
        return _result(
            case.category,
            rule_id,
            checks,
            supported=list(hypothesis.evidence_ids),
            conflicting=[],
        )

    settlement_id = settlement_items[0][1]
    settlement = snapshot.settlements.get(settlement_id)
    row = snapshot.ledger_entries.get(ledger_items[0][1])
    if settlement is None or row is None:
        checks.check(
            "records-exist",
            f"settlement {settlement_id} and ledger row {ledger_items[0][1]} exist",
            False,
            ReasonCode.UNKNOWN_EVIDENCE_ID.value,
        )
        return _result(
            case.category, rule_id, checks, supported=list(hypothesis.evidence_ids), conflicting=[]
        )

    identity_ok = (
        row.source_type == "SETTLEMENT"
        and row.source_reference == settlement_id
        and row.account_code == ACCOUNT_BANK
    )
    checks.check(
        "booking-identity",
        f"ledger {row.ledger_entry_id} is the bank booking of settlement {settlement_id}",
        identity_ok,
        ReasonCode.REFERENCE_CONFLICT.value,
    )

    net = int(settlement.net_amount_paise)
    signed = int(row.signed_amount_paise)
    amount_ok = signed == net and row.currency == settlement.currency
    checks.check(
        "amount-consistent",
        f"booking signed({signed}) == settlement net({net}) and currency matches",
        amount_ok,
        ReasonCode.AMOUNT_MISMATCH.value,
    )

    booked = row.accounting_date
    inside = settlement.window_start_date <= booked <= settlement.window_end_date
    checks.check(
        "outside-own-window",
        f"booked({booked.isoformat()}) outside own window "
        f"[{settlement.window_start_date.isoformat()}, {settlement.window_end_date.isoformat()}]",
        not inside,
        ReasonCode.UNSUPPORTED_CATEGORY.value,
    )

    adjacency = timedelta(days=TIMING_ADJACENCY_DAYS)
    within_adjacency = (
        settlement.window_start_date - adjacency <= booked <= settlement.window_end_date + adjacency
    )
    checks.check(
        "within-adjacency",
        f"booked({booked.isoformat()}) within +/-{TIMING_ADJACENCY_DAYS}d of the window",
        within_adjacency,
        ReasonCode.OUTSIDE_ALLOWED_WINDOW.value,
    )

    competing_ids: list[str] = []
    if settlement.utr is None or settlement_id in snapshot.twin_settlement_ids:
        for other_id, other in sorted(snapshot.settlements.items()):
            if other_id == settlement_id or int(other.net_amount_paise) != net:
                continue
            if other.utr is not None:
                continue
            other_row = _booking_shifted(other_id, snapshot)
            if other_row is None:
                continue
            if (
                other.window_start_date - adjacency
                <= other_row.accounting_date
                <= other.window_end_date + adjacency
            ):
                competing_ids.append(f"SETTLEMENT:{other_id}")
    unique = not competing_ids and settlement_id not in snapshot.twin_settlement_ids
    checks.check(
        "candidate-unique",
        f"no second equal-net UTR-less settlement shifted within adjacency (found {competing_ids})",
        unique,
        ReasonCode.NON_UNIQUE_EVIDENCE.value,
    )
    if not unique:
        return _result(
            case.category,
            rule_id,
            checks,
            supported=list(hypothesis.evidence_ids),
            conflicting=competing_ids,
            inconclusive=True,
            competing=tuple(sorted([f"SETTLEMENT:{settlement_id}", *competing_ids])),
            missing_discriminator="a unique UTR distinguishing the equal-amount settlements",
            next_step="obtain the UTRs from the settlement and bank statements",
        )

    variance_ok = case.variance_paise == 0
    checks.check(
        "variance-zero",
        f"case.variance({case.variance_paise}) == 0 (attribution only)",
        variance_ok,
        ReasonCode.AMOUNT_MISMATCH.value,
    )
    checks.check(
        "value-unchanged",
        f"delta == 0; booking signed({signed}) == net({net}) in both periods",
        signed == net,
        ReasonCode.CONTROL_TOTAL_VIOLATION.value,
    )

    if checks.failed():
        return _result(
            case.category,
            rule_id,
            checks,
            supported=[f"SETTLEMENT:{settlement_id}", f"LEDGER_ENTRY:{row.ledger_entry_id}"],
            conflicting=[],
        )

    return _result(
        case.category,
        rule_id,
        checks,
        supported=[f"SETTLEMENT:{settlement_id}", f"LEDGER_ENTRY:{row.ledger_entry_id}"],
        conflicting=[],
        proposed_delta=0,
        rejected=(
            RejectedAlternative(
                description="booking belongs to a different settlement",
                reason_codes=(ReasonCode.REFERENCE_CONFLICT.value,),
                evidence_ids=(f"SETTLEMENT:{settlement_id}",),
            ),
            RejectedAlternative(
                description="economic value differs across periods",
                reason_codes=(ReasonCode.AMOUNT_MISMATCH.value,),
                evidence_ids=(f"LEDGER_ENTRY:{row.ledger_entry_id}",),
            ),
        ),
        uncertainty=("period attribution only; total economic value unchanged",),
    )


# ---------------------------------------------------------------------------
# PRD 9.6 — ambiguity: structurally never a PASS.
# ---------------------------------------------------------------------------

_AMBIGUITY_GUIDANCE: dict[str, tuple[str, str]] = {
    ReasonCode.NON_UNIQUE_EVIDENCE.value: (
        "a unique distinguishing identifier (UTR or posting reference)",
        "obtain the missing identifier from the owning source system and re-verify",
    ),
    ReasonCode.MISSING_EVIDENCE.value: (
        "the required counterpart record for this case",
        "request the missing statement or export covering the case window",
    ),
    ReasonCode.AMOUNT_MISMATCH.value: (
        "an amount and reference pairing consistent across sources",
        "confirm with the finance owner which record is authoritative",
    ),
    ReasonCode.REFERENCE_CONFLICT.value: (
        "an unambiguous reference pairing across sources",
        "confirm with the finance owner which reference is authoritative",
    ),
    ReasonCode.CONTROL_TOTAL_VIOLATION.value: (
        "member contributions that conserve the settlement total",
        "re-export the settlement together with its member events",
    ),
}


def verify_ambiguity(
    case: CaseRecord, hypothesis: StructuredHypothesis, snapshot: EvidenceSnapshot
) -> VerifierResult:
    rule_id = V_AMBIGUITY
    checks = _Checks()
    cited = sorted(hypothesis.evidence_ids)
    checks.check(
        "ambiguity-inherent",
        f"{len(cited)} cited candidates satisfy the available constraints or a required record "
        "is missing; no deterministic discriminator exists",
        False,
        ReasonCode.NON_UNIQUE_EVIDENCE.value,
    )
    primary = next(
        (code for code in case.reason_codes if code in _AMBIGUITY_GUIDANCE),
        ReasonCode.NON_UNIQUE_EVIDENCE.value,
    )
    missing_discriminator, next_step = _AMBIGUITY_GUIDANCE[primary]
    return _result(
        case.category,
        rule_id,
        checks,
        supported=cited,
        conflicting=[],
        inconclusive=True,
        competing=tuple(cited),
        missing_discriminator=missing_discriminator,
        next_step=next_step,
        extra_codes=tuple(case.reason_codes),
    )
