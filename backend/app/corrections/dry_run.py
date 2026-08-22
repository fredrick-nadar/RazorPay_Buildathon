"""Ledger correction dry-run (PRD 11.2) — calculation only, never mutation.

``preview_correction`` is a pure function over the case, a passing proof, and
the evidence snapshot. It returns a :class:`DryRunResult` whose status is
always ``DRAFT``: Phase 3 persists these previews as run outputs only. A
preview is not an applied correction; it never creates a ledger entry — of
any origin — and never touches imported rows, normalized rows, or financial
postings (approved clarification; PRD 5.16, 11.2).

The delta is independently re-derived from the snapshot records and must
exactly zero the case variance, otherwise the preview is refused. A dry run
on a non-PASS proof is refused outright (PRD Phase 3 negative test).
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from app.corrections.authority import AuthorityDecision
from app.domain.enums import CorrectionStatus, EntryOrigin, ExceptionCategory, VerifierStatus
from app.reconciliation.detectors import CaseRecord
from app.reconciliation.rules import ACCOUNT_CLEARING
from app.verifier.models import parse_evidence_id
from app.verifier.snapshot import EvidenceSnapshot

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from app.verifier.proof import ProofPackage


class CorrectionRefused(RuntimeError):
    """The dry run was refused: non-PASS proof or a delta that fails its checks."""


@dataclass(frozen=True)
class ProposedLedgerEntry:
    """Value object describing the entry a correction *would* create.

    This is a proposal only: no ``LedgerEntryRecord`` with a simulated
    correction origin is ever constructed by Phase 3 code.
    """

    account_code: str
    accounting_date: str
    currency: str
    signed_amount_paise: int
    source_type: str
    source_reference: str
    description: str
    entry_origin: str
    reverses_entry_id: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "account_code": self.account_code,
            "accounting_date": self.accounting_date,
            "currency": self.currency,
            "signed_amount_paise": self.signed_amount_paise,
            "source_type": self.source_type,
            "source_reference": self.source_reference,
            "description": self.description,
            "entry_origin": self.entry_origin,
            "reverses_entry_id": self.reverses_entry_id,
        }


@dataclass(frozen=True)
class DryRunResult:
    """DRAFT correction preview (PRD 6.11 correction record, DRAFT state)."""

    correction_id: str
    case_id: str
    proof_id: str
    status: str
    proposed_entry: ProposedLedgerEntry | None
    target_ledger_entry_id: str | None
    account_code: str | None
    proposed_delta_paise: int
    variance_before_paise: int
    variance_after_paise: int
    totals_before_paise: dict[str, int]
    totals_after_paise: dict[str, int]
    warnings: tuple[str, ...]
    remaining_uncertainty: tuple[str, ...]
    verifier_rule_id: str
    verifier_rule_version: str
    authority_decision: str
    requires_approval: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "correction_id": self.correction_id,
            "case_id": self.case_id,
            "proof_id": self.proof_id,
            "status": self.status,
            "proposed_entry": self.proposed_entry.to_json() if self.proposed_entry else None,
            "target_ledger_entry_id": self.target_ledger_entry_id,
            "account_code": self.account_code,
            "proposed_delta_paise": self.proposed_delta_paise,
            "variance_before_paise": self.variance_before_paise,
            "variance_after_paise": self.variance_after_paise,
            "totals_before_paise": self.totals_before_paise,
            "totals_after_paise": self.totals_after_paise,
            "warnings": list(self.warnings),
            "remaining_uncertainty": list(self.remaining_uncertainty),
            "verifier_rule_id": self.verifier_rule_id,
            "verifier_rule_version": self.verifier_rule_version,
            "authority_decision": self.authority_decision,
            "requires_approval": self.requires_approval,
        }


def _expected_delta(case: CaseRecord, snapshot: EvidenceSnapshot) -> int | None:
    """Independent per-category delta derivation from snapshot records."""
    if case.category == ExceptionCategory.DUPLICATE_LEDGER_POSTING:
        source = next(
            (
                parse_evidence_id(f"{item.record_type}:{item.record_id}")
                for item in case.evidence
                if item.record_type != "LEDGER_ENTRY"
            ),
            None,
        )
        if source is None:
            return None
        source_type, source_id = source
        rows = snapshot.ledger_rows_by_source.get((source_type, source_id), ())
        if not rows:
            return None
        signed = int(rows[0].signed_amount_paise)
        return -(len(rows) - 1) * signed
    if case.category == ExceptionCategory.MISSING_REFUND_POSTING:
        refund_id = next(
            (item.record_id for item in case.evidence if item.record_type == "REFUND"), None
        )
        refund = snapshot.refunds.get(refund_id or "")
        return None if refund is None else -int(refund.refund_amount_paise)
    if case.category == ExceptionCategory.SETTLEMENT_TIMING_WINDOW_SHIFT:
        return 0
    return None


def _correction_id(case_id: str, proof_id: str, delta: int, account: str | None) -> str:
    digest = sha256(f"{case_id}|{proof_id}|{delta}|{account or ''}".encode()).hexdigest()[:12]
    return f"corr-{digest}"


def preview_correction(
    case: CaseRecord,
    proof: ProofPackage,
    snapshot: EvidenceSnapshot,
    authority: AuthorityDecision,
) -> DryRunResult:
    """Compute a DRAFT correction preview; refuse anything unverifiable."""
    if proof.verifier_status != VerifierStatus.PASS:
        raise CorrectionRefused(
            f"dry run refused: proof {proof.proof_id} verifier status is "
            f"{proof.verifier_status.value}, not PASS"
        )
    if proof.case_id != case.case_id:
        raise CorrectionRefused(
            f"dry run refused: proof {proof.proof_id} belongs to case {proof.case_id}"
        )
    delta = proof.proposed_delta_paise
    if delta is None:
        raise CorrectionRefused("dry run refused: PASS proof carries no derived delta")

    expected = _expected_delta(case, snapshot)
    if expected is None or delta != expected:
        raise CorrectionRefused(
            f"dry run refused: delta {delta} does not match the independent derivation "
            f"{expected} for category {case.category.value}"
        )

    variance_before = case.variance_paise
    variance_after = variance_before + delta
    if delta != 0 and variance_after != 0:
        raise CorrectionRefused(
            f"dry run refused: variance {variance_before} + delta {delta} == {variance_after} != 0"
        )

    proposed_entry: ProposedLedgerEntry | None = None
    target_ledger_entry_id: str | None = None
    account_code: str | None = None
    warnings: list[str] = [
        "preview only: application requires human approval (synthetic merchant policy)"
    ]

    if case.category == ExceptionCategory.DUPLICATE_LEDGER_POSTING:
        source = next(
            parse_evidence_id(f"{item.record_type}:{item.record_id}")
            for item in case.evidence
            if item.record_type != "LEDGER_ENTRY"
        )
        if source is None:
            raise CorrectionRefused("dry run refused: duplicate case source evidence is malformed")
        source_type, source_id = source
        rows = sorted(
            snapshot.ledger_rows_by_source.get((source_type, source_id), ()),
            key=lambda row: row.ledger_entry_id,
        )
        if not rows:
            raise CorrectionRefused("dry run refused: no ledger rows cite the source record")
        target = rows[-1]
        target_ledger_entry_id = target.ledger_entry_id
        account_code = target.account_code
        proposed_entry = ProposedLedgerEntry(
            account_code=target.account_code,
            accounting_date=target.accounting_date.isoformat(),
            currency=target.currency,
            signed_amount_paise=delta,
            source_type=source_type,
            source_reference=source_id,
            description=f"reverse duplicate ledger posting for {source_id}",
            entry_origin=EntryOrigin.SIMULATED_CORRECTION.value,
            reverses_entry_id=target.ledger_entry_id,
        )
    elif case.category == ExceptionCategory.MISSING_REFUND_POSTING:
        refund_id = next(item.record_id for item in case.evidence if item.record_type == "REFUND")
        refund = snapshot.refunds.get(refund_id)
        if refund is None:
            raise CorrectionRefused("dry run refused: refund record missing from snapshot")
        account_code = ACCOUNT_CLEARING
        proposed_entry = ProposedLedgerEntry(
            account_code=ACCOUNT_CLEARING,
            accounting_date=refund.created_at_utc.date().isoformat(),
            currency=refund.currency,
            signed_amount_paise=delta,
            source_type="REFUND",
            source_reference=refund.refund_id,
            description=f"add missing refund posting for {refund.refund_id}",
            entry_origin=EntryOrigin.SIMULATED_CORRECTION.value,
            reverses_entry_id=None,
        )
    else:  # timing-window shift: attribution only, no ledger entry
        warnings.append(
            "period attribution only: no economic delta, so no ledger entry is proposed"
        )

    totals_before: dict[str, int] = {"ledger_total_paise": 0}
    for row in snapshot.ledger_entries.values():
        totals_before["ledger_total_paise"] += int(row.signed_amount_paise)
        key = f"account_paise:{row.account_code}"
        totals_before[key] = totals_before.get(key, 0) + int(row.signed_amount_paise)
    totals_after = dict(totals_before)
    totals_after["ledger_total_paise"] += delta
    if account_code is not None:
        key = f"account_paise:{account_code}"
        totals_after[key] = totals_after.get(key, 0) + delta
    totals_before["case_variance_abs_paise"] = abs(variance_before)
    totals_after["case_variance_abs_paise"] = abs(variance_after)

    return DryRunResult(
        correction_id=_correction_id(case.case_id, proof.proof_id, delta, account_code),
        case_id=case.case_id,
        proof_id=proof.proof_id,
        status=CorrectionStatus.DRAFT.value,
        proposed_entry=proposed_entry,
        target_ledger_entry_id=target_ledger_entry_id,
        account_code=account_code,
        proposed_delta_paise=delta,
        variance_before_paise=variance_before,
        variance_after_paise=variance_after,
        totals_before_paise=dict(sorted(totals_before.items())),
        totals_after_paise=dict(sorted(totals_after.items())),
        warnings=tuple(warnings),
        remaining_uncertainty=tuple(proof.uncertainty),
        verifier_rule_id=proof.verifier_rule_id,
        verifier_rule_version=proof.verifier_rule_version,
        authority_decision=authority.decision.value,
        requires_approval=authority.requires_approval,
    )
