"""Deterministic verification engine (PRD 9.1, 9.2).

``verify_case`` runs the global verification requirements before any
category logic: the hypothesis belongs to the case, every evidence id exists
in the snapshot, currencies agree, and no cited evidence was already claimed
by another passing proof. Only then does the category verifier run.

``verify_cases`` orchestrates one system-generated hypothesis per case in the
deterministic detector order, builds a proof for every outcome, computes a
DRAFT dry-run preview for every PASS, applies authority classification, and
returns updated case records. Phase 3 has no model: hypotheses are
deterministic, and the Phase 4 investigator will route its proposals through
this same entry point.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from statistics import median
from typing import Any

from app.corrections.authority import AuthorityDecision, classify_authority
from app.corrections.dry_run import DryRunResult, preview_correction
from app.domain.enums import (
    ExceptionCategory,
    HypothesisStatus,
    ReasonCode,
    VerifierStatus,
)
from app.domain.records import AcceptedRecords
from app.reconciliation.detectors import CaseRecord
from app.verifier.categories import (
    verify_ambiguity,
    verify_duplicate_ledger,
    verify_missing_refund,
    verify_timing_window,
)
from app.verifier.models import (
    Equation,
    StructuredHypothesis,
    VerifierResult,
    hypothesis_id_for,
    parse_evidence_id,
)
from app.verifier.proof import ProofPackage, build_proof, proof_is_complete
from app.verifier.rules import (
    V_AMBIGUITY,
    V_DUPLICATE_LEDGER,
    V_MISSING_REFUND,
    V_TIMING_WINDOW,
    VERIFIER_RULE_VERSIONS,
    verifier_rule_manifest,
)
from app.verifier.snapshot import EvidenceSnapshot, build_evidence_snapshot

EvidenceKey = tuple[str, str]

_SYSTEM_CLAIMS: dict[ExceptionCategory, str] = {
    ExceptionCategory.DUPLICATE_LEDGER_POSTING: (
        "two or more ledger rows post one source-side event; reversing the extra "
        "posting restores the expected balance"
    ),
    ExceptionCategory.MISSING_REFUND_POSTING: (
        "a processed refund has no ledger posting inside the posting window; adding "
        "the signed entry restores the expected balance"
    ),
    ExceptionCategory.SETTLEMENT_TIMING_WINDOW_SHIFT: (
        "the settlement booking belongs to an adjacent accounting window; attribution "
        "shifts, total economic value does not"
    ),
    ExceptionCategory.AMBIGUOUS_EVIDENCE: (
        "the available evidence admits multiple candidates or misses a required record"
    ),
}

_RULE_FOR_CATEGORY: dict[ExceptionCategory, str] = {
    ExceptionCategory.DUPLICATE_LEDGER_POSTING: V_DUPLICATE_LEDGER,
    ExceptionCategory.MISSING_REFUND_POSTING: V_MISSING_REFUND,
    ExceptionCategory.SETTLEMENT_TIMING_WINDOW_SHIFT: V_TIMING_WINDOW,
    ExceptionCategory.AMBIGUOUS_EVIDENCE: V_AMBIGUITY,
}


def build_system_hypotheses(cases: list[CaseRecord]) -> tuple[StructuredHypothesis, ...]:
    """One deterministic hypothesis per case (Phase 3: no model involved)."""
    hypotheses: list[StructuredHypothesis] = []
    for case in cases:
        evidence_ids = tuple(
            sorted(f"{item.record_type}:{item.record_id}" for item in case.evidence)
        )
        hypotheses.append(
            StructuredHypothesis(
                hypothesis_id=hypothesis_id_for(case.case_id, case.category, evidence_ids),
                case_id=case.case_id,
                category=case.category,
                claim=_SYSTEM_CLAIMS[case.category],
                evidence_ids=evidence_ids,
            )
        )
    return tuple(hypotheses)


def _global_failure(
    case: CaseRecord,
    hypothesis: StructuredHypothesis,
    codes: tuple[str, ...],
    *,
    supported: list[str],
    conflicting: list[str],
) -> VerifierResult:
    rule_id = _RULE_FOR_CATEGORY[case.category]
    return VerifierResult(
        status=VerifierStatus.FAIL,
        category=case.category,
        rule_id=rule_id,
        rule_version=VERIFIER_RULE_VERSIONS[rule_id],
        reason_codes=codes,
        equations=(
            Equation(
                "global-precheck",
                f"global verification requirements failed: {', '.join(codes)}",
                False,
            ),
        ),
        supported_evidence_ids=tuple(sorted(set(supported))),
        conflicting_evidence_ids=tuple(sorted(set(conflicting))),
        proposed_delta_paise=None,
    )


def verify_case(
    case: CaseRecord,
    hypothesis: StructuredHypothesis,
    snapshot: EvidenceSnapshot,
    consumed_evidence: frozenset[EvidenceKey] = frozenset(),
) -> VerifierResult:
    """Run the PRD 9.2 global requirements, then the category verifier."""
    if hypothesis.case_id != case.case_id or hypothesis.category != case.category:
        return _global_failure(
            case,
            hypothesis,
            (ReasonCode.UNSUPPORTED_CATEGORY.value,),
            supported=[],
            conflicting=list(hypothesis.evidence_ids),
        )

    parsed: list[EvidenceKey] = []
    malformed: list[str] = []
    for evidence_id in hypothesis.evidence_ids:
        item = parse_evidence_id(evidence_id)
        if item is None:
            malformed.append(evidence_id)
        else:
            parsed.append(item)
    if malformed:
        return _global_failure(
            case,
            hypothesis,
            (ReasonCode.UNKNOWN_EVIDENCE_ID.value,),
            supported=[],
            conflicting=malformed,
        )

    unknown = [f"{t}:{i}" for t, i in parsed if not snapshot.contains(t, i)]
    if unknown:
        return _global_failure(
            case,
            hypothesis,
            (ReasonCode.UNKNOWN_EVIDENCE_ID.value,),
            supported=[],
            conflicting=unknown,
        )

    mismatched = [f"{t}:{i}" for t, i in parsed if snapshot.currency_of(t, i) != case.currency]
    if mismatched:
        return _global_failure(
            case,
            hypothesis,
            (ReasonCode.CURRENCY_MISMATCH.value,),
            supported=[],
            conflicting=mismatched,
        )

    reused = [f"{t}:{i}" for t, i in parsed if (t, i) in consumed_evidence]
    if reused:
        return _global_failure(
            case,
            hypothesis,
            (ReasonCode.RECORD_ALREADY_CONSUMED.value,),
            supported=[],
            conflicting=reused,
        )

    if case.category == ExceptionCategory.DUPLICATE_LEDGER_POSTING:
        return verify_duplicate_ledger(case, hypothesis, snapshot)
    if case.category == ExceptionCategory.MISSING_REFUND_POSTING:
        return verify_missing_refund(case, hypothesis, snapshot)
    if case.category == ExceptionCategory.SETTLEMENT_TIMING_WINDOW_SHIFT:
        return verify_timing_window(case, hypothesis, snapshot)
    if case.category == ExceptionCategory.AMBIGUOUS_EVIDENCE:
        return verify_ambiguity(case, hypothesis, snapshot)
    return _global_failure(
        case,
        hypothesis,
        (ReasonCode.UNSUPPORTED_CATEGORY.value,),
        supported=[],
        conflicting=[],
    )


def _claimable_evidence_keys(case: CaseRecord) -> set[EvidenceKey]:
    """Evidence keys claimed exclusively by a PASS proof.

    Context records can legitimately appear in several cases. For example, a
    settlement can provide context for multiple missing-refund cases; claiming
    it globally would turn the second correct case into a false
    RECORD_ALREADY_CONSUMED failure. Only the records whose financial fact is
    being resolved are exclusive in Phase 3.
    """
    if case.category == ExceptionCategory.DUPLICATE_LEDGER_POSTING:
        return {(item.record_type, item.record_id) for item in case.evidence}
    if case.category == ExceptionCategory.MISSING_REFUND_POSTING:
        return {
            (item.record_type, item.record_id)
            for item in case.evidence
            if item.record_type == "REFUND"
        }
    if case.category == ExceptionCategory.SETTLEMENT_TIMING_WINDOW_SHIFT:
        return {
            (item.record_type, item.record_id)
            for item in case.evidence
            if item.record_type in {"SETTLEMENT", "LEDGER_ENTRY"}
        }
    return set()


@dataclass(frozen=True)
class CaseVerification:
    """One case's verification artifacts (updated case, proof, dry run)."""

    case: CaseRecord
    hypothesis: StructuredHypothesis
    result: VerifierResult
    proof: ProofPackage
    dry_run: DryRunResult | None
    authority: AuthorityDecision
    duration_ms: float


@dataclass(frozen=True)
class VerificationOutcome:
    """Every verification artifact for one run, in deterministic case order."""

    verifications: tuple[CaseVerification, ...]
    latency_ms: dict[str, float]

    @property
    def cases(self) -> tuple[CaseRecord, ...]:
        return tuple(item.case for item in self.verifications)

    @property
    def hypotheses(self) -> tuple[StructuredHypothesis, ...]:
        return tuple(item.hypothesis for item in self.verifications)

    @property
    def proofs(self) -> tuple[ProofPackage, ...]:
        return tuple(item.proof for item in self.verifications)

    @property
    def dry_runs(self) -> tuple[DryRunResult, ...]:
        return tuple(item.dry_run for item in self.verifications if item.dry_run is not None)

    def summary(self) -> dict[str, Any]:
        """The ``verification`` block of the run output contract."""
        counts_by_status: dict[str, int] = {}
        counts_by_category_status: dict[str, dict[str, int]] = {}
        for item in self.verifications:
            status = item.case.status.value
            counts_by_status[status] = counts_by_status.get(status, 0) + 1
            category = item.case.category.value
            counts_by_category_status.setdefault(category, {})
            counts_by_category_status[category][status] = (
                counts_by_category_status[category].get(status, 0) + 1
            )
        verifier_status_counts = {"PASS": 0, "FAIL": 0, "INCONCLUSIVE": 0}
        dry_run_error = 0
        pass_count = 0
        complete_proofs = 0
        for item in self.verifications:
            verifier_status_counts[item.result.status.value] += 1
            if item.result.status == VerifierStatus.PASS:
                pass_count += 1
                if proof_is_complete(item.proof):
                    complete_proofs += 1
            if item.dry_run is not None:
                dry_run_error += abs(item.dry_run.variance_after_paise)
        return {
            "verifier_rule_manifest": verifier_rule_manifest(),
            "case_status_counts": dict(sorted(counts_by_status.items())),
            "counts_by_category_status": {
                category: dict(sorted(statuses.items()))
                for category, statuses in sorted(counts_by_category_status.items())
            },
            "verifier_status_counts": verifier_status_counts,
            "proof_count": len(self.proofs),
            "passing_proof_completeness": {
                "numerator": complete_proofs,
                "denominator": pass_count,
            },
            "dry_run_count": len(self.dry_runs),
            "dry_run_abs_variance_after_paise": dry_run_error,
            "latency_ms": dict(sorted(self.latency_ms.items())),
            "results": [
                {
                    "case_id": item.case.case_id,
                    "hypothesis_id": item.hypothesis.hypothesis_id,
                    "proof_id": item.proof.proof_id,
                    "category": item.case.category.value,
                    "verifier_status": item.result.status.value,
                    "rule_id": item.result.rule_id,
                    "rule_version": item.result.rule_version,
                    "reason_codes": list(item.result.reason_codes),
                    "equation_count": len(item.result.equations),
                    "supported_evidence_count": len(item.result.supported_evidence_ids),
                    "conflicting_evidence_count": len(item.result.conflicting_evidence_ids),
                    "proposed_delta_paise": item.result.proposed_delta_paise,
                    "case_status": item.case.status.value,
                    "authority_decision": item.authority.decision.value,
                    "requires_approval": item.authority.requires_approval,
                    "dry_run_variance_after_paise": (
                        item.dry_run.variance_after_paise if item.dry_run else None
                    ),
                    "canonical_hash": item.proof.canonical_hash,
                }
                for item in self.verifications
            ],
        }


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def verify_cases(records: AcceptedRecords, cases: list[CaseRecord]) -> VerificationOutcome:
    """Verify every case deterministically and classify authority."""
    snapshot = build_evidence_snapshot(records)
    hypotheses = {item.case_id: item for item in build_system_hypotheses(cases)}
    created_at_iso = _utc_now_iso()
    consumed: set[EvidenceKey] = set()
    verifications: list[CaseVerification] = []
    durations: list[float] = []
    started = time.perf_counter()

    for case in cases:
        hypothesis = hypotheses[case.case_id]
        case_started = time.perf_counter()
        result = verify_case(case, hypothesis, snapshot, frozenset(consumed))
        duration_ms = (time.perf_counter() - case_started) * 1000.0
        durations.append(duration_ms)

        authority = classify_authority(result.status, result.proposed_delta_paise)
        updated_case = replace(
            case,
            status=authority.decision,
            proposed_delta_paise=(
                result.proposed_delta_paise if result.status == VerifierStatus.PASS else None
            ),
        )
        proof = build_proof(case, hypothesis, result, authority, created_at_iso)
        dry_run: DryRunResult | None = None
        if result.status == VerifierStatus.PASS:
            dry_run = preview_correction(updated_case, proof, snapshot, authority)
            proof = replace(proof, dry_run=dry_run)
            consumed |= _claimable_evidence_keys(case)

        if result.status == VerifierStatus.PASS:
            hypothesis_status = HypothesisStatus.SUPPORTED
        elif result.status == VerifierStatus.FAIL:
            hypothesis_status = HypothesisStatus.REJECTED
        else:
            hypothesis_status = HypothesisStatus.INCONCLUSIVE
        final_hypothesis = replace(
            hypothesis,
            status=hypothesis_status,
            reason_codes=result.reason_codes,
        )

        verifications.append(
            CaseVerification(
                case=updated_case,
                hypothesis=final_hypothesis,
                result=result,
                proof=proof,
                dry_run=dry_run,
                authority=authority,
                duration_ms=duration_ms,
            )
        )

    total_ms = (time.perf_counter() - started) * 1000.0
    latency = {
        "verify_total_ms": round(total_ms, 3),
        "median_case_ms": round(median(durations), 3) if durations else 0.0,
        "max_case_ms": round(max(durations), 3) if durations else 0.0,
    }
    return VerificationOutcome(verifications=tuple(verifications), latency_ms=latency)


__all__ = [
    "CaseVerification",
    "EvidenceKey",
    "VerificationOutcome",
    "build_system_hypotheses",
    "verify_case",
    "verify_cases",
]
