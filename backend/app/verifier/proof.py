"""Canonical proof packages (PRD 6.10, 9.1 output contract).

A proof is content-addressed: ``proof_id`` and ``canonical_hash`` derive from
the canonical JSON of every content field (sorted keys, concrete integers),
excluding creation timestamps, so identical verification over identical
evidence always produces the identical proof id. Staleness is mechanical: any
change to the reconciliation or verifier rule manifests — or to the specific
verifier rule version — makes the proof stale
(:func:`proof_stale_reasons`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.corrections.authority import AuthorityDecision
from app.corrections.dry_run import DryRunResult
from app.domain.enums import ExceptionCategory, VerifierStatus
from app.reconciliation.detectors import CaseRecord
from app.reconciliation.rules import rule_manifest
from app.verifier.models import (
    Equation,
    RejectedAlternative,
    StructuredHypothesis,
    VerifierResult,
)
from app.verifier.rules import (
    VERIFIER_RULE_MANIFEST_VERSION,
    VERIFIER_RULE_VERSIONS,
    verifier_rule_manifest,
)


@dataclass(frozen=True)
class ProofPackage:
    """Machine-readable proof for one verification outcome (PRD 6.10)."""

    proof_id: str
    case_id: str
    hypothesis_id: str
    claim: str
    category: ExceptionCategory
    evidence_ids: tuple[str, ...]
    supported_evidence_ids: tuple[str, ...]
    conflicting_evidence_ids: tuple[str, ...]
    equations: tuple[Equation, ...]
    rejected_alternatives: tuple[RejectedAlternative, ...]
    verifier_status: VerifierStatus
    verifier_rule_id: str
    verifier_rule_version: str
    recon_manifest_fingerprint: str
    verifier_manifest_fingerprint: str
    proposed_delta_paise: int | None
    dry_run: DryRunResult | None
    authority_decision: str
    requires_approval: bool
    uncertainty: tuple[str, ...]
    competing_candidates: tuple[str, ...]
    missing_discriminator: str | None
    recommended_next_step: str | None
    canonical_hash: str
    created_at_utc: str

    def to_json(self) -> dict[str, Any]:
        return {
            "proof_id": self.proof_id,
            "case_id": self.case_id,
            "hypothesis_id": self.hypothesis_id,
            "claim": self.claim,
            "category": self.category.value,
            "evidence_ids": list(self.evidence_ids),
            "supported_evidence_ids": list(self.supported_evidence_ids),
            "conflicting_evidence_ids": list(self.conflicting_evidence_ids),
            "equations": [
                {
                    "label": equation.label,
                    "expression": equation.expression,
                    "holds": equation.holds,
                }
                for equation in self.equations
            ],
            "rejected_alternatives": [
                {
                    "description": alternative.description,
                    "reason_codes": list(alternative.reason_codes),
                    "evidence_ids": list(alternative.evidence_ids),
                }
                for alternative in self.rejected_alternatives
            ],
            "verifier_status": self.verifier_status.value,
            "verifier_rule_id": self.verifier_rule_id,
            "verifier_rule_version": self.verifier_rule_version,
            "recon_manifest_fingerprint": self.recon_manifest_fingerprint,
            "verifier_manifest_fingerprint": self.verifier_manifest_fingerprint,
            "proposed_delta_paise": self.proposed_delta_paise,
            "dry_run": self.dry_run.to_json() if self.dry_run else None,
            "authority_decision": self.authority_decision,
            "requires_approval": self.requires_approval,
            "uncertainty": list(self.uncertainty),
            "competing_candidates": list(self.competing_candidates),
            "missing_discriminator": self.missing_discriminator,
            "recommended_next_step": self.recommended_next_step,
            "canonical_hash": self.canonical_hash,
            "created_at_utc": self.created_at_utc,
        }


def manifest_fingerprint(manifest: dict[str, dict[str, str]]) -> str:
    return sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def recon_manifest_fingerprint() -> str:
    return manifest_fingerprint(rule_manifest())


def verifier_manifest_fingerprint() -> str:
    return manifest_fingerprint(verifier_rule_manifest())


def _canonical_content(
    case: CaseRecord,
    hypothesis: StructuredHypothesis,
    result: VerifierResult,
    authority: AuthorityDecision,
) -> dict[str, Any]:
    """Content hashed into ``proof_id``/``canonical_hash``.

    The dry-run result deliberately stays outside the canonical content: it
    is derived from the same verified delta and cites the proof id, so
    including it would be circular.
    """

    return {
        "case_id": case.case_id,
        "hypothesis_id": hypothesis.hypothesis_id,
        "claim": hypothesis.claim,
        "category": case.category.value,
        "evidence_ids": sorted(hypothesis.evidence_ids),
        "supported_evidence_ids": sorted(result.supported_evidence_ids),
        "conflicting_evidence_ids": sorted(result.conflicting_evidence_ids),
        "equations": [
            [equation.label, equation.expression, equation.holds] for equation in result.equations
        ],
        "rejected_alternatives": [
            [
                alternative.description,
                sorted(alternative.reason_codes),
                sorted(alternative.evidence_ids),
            ]
            for alternative in result.rejected_alternatives
        ],
        "verifier_status": result.status.value,
        "verifier_rule_id": result.rule_id,
        "verifier_rule_version": result.rule_version,
        "recon_manifest_fingerprint": recon_manifest_fingerprint(),
        "verifier_manifest_fingerprint": verifier_manifest_fingerprint(),
        "proposed_delta_paise": result.proposed_delta_paise,
        "authority_decision": authority.decision.value,
        "requires_approval": authority.requires_approval,
        "uncertainty": sorted(result.uncertainty),
        "competing_candidates": sorted(result.competing_candidates),
        "missing_discriminator": result.missing_discriminator,
        "recommended_next_step": result.recommended_next_step,
    }


def build_proof(
    case: CaseRecord,
    hypothesis: StructuredHypothesis,
    result: VerifierResult,
    authority: AuthorityDecision,
    created_at_iso: str,
) -> ProofPackage:
    content = _canonical_content(case, hypothesis, result, authority)
    canonical_hash = sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ProofPackage(
        proof_id=f"proof-{canonical_hash[:12]}",
        case_id=case.case_id,
        hypothesis_id=hypothesis.hypothesis_id,
        claim=hypothesis.claim,
        category=case.category,
        evidence_ids=tuple(sorted(hypothesis.evidence_ids)),
        supported_evidence_ids=result.supported_evidence_ids,
        conflicting_evidence_ids=result.conflicting_evidence_ids,
        equations=result.equations,
        rejected_alternatives=result.rejected_alternatives,
        verifier_status=result.status,
        verifier_rule_id=result.rule_id,
        verifier_rule_version=result.rule_version,
        recon_manifest_fingerprint=content["recon_manifest_fingerprint"],
        verifier_manifest_fingerprint=content["verifier_manifest_fingerprint"],
        proposed_delta_paise=result.proposed_delta_paise,
        dry_run=None,
        authority_decision=authority.decision.value,
        requires_approval=authority.requires_approval,
        uncertainty=result.uncertainty,
        competing_candidates=result.competing_candidates,
        missing_discriminator=result.missing_discriminator,
        recommended_next_step=result.recommended_next_step,
        canonical_hash=canonical_hash,
        created_at_utc=created_at_iso,
    )


def proof_stale_reasons(
    proof: ProofPackage,
    *,
    current_recon_manifest: dict[str, dict[str, str]] | None = None,
    current_verifier_manifest: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """Mechanical staleness: rule-manifest or rule-version drift."""
    reasons: list[str] = []
    current_version = VERIFIER_RULE_VERSIONS.get(proof.verifier_rule_id)
    if current_version != proof.verifier_rule_version:
        reasons.append(
            f"verifier rule {proof.verifier_rule_id} version {proof.verifier_rule_version} "
            f"!= current {current_version}"
        )
    if current_recon_manifest is not None and (
        manifest_fingerprint(current_recon_manifest) != proof.recon_manifest_fingerprint
    ):
        reasons.append("reconciliation rule manifest changed since the proof was built")
    if current_verifier_manifest is not None and (
        manifest_fingerprint(current_verifier_manifest) != proof.verifier_manifest_fingerprint
    ):
        reasons.append(
            f"verifier rule manifest changed since the proof was built "
            f"(manifest {VERIFIER_RULE_MANIFEST_VERSION})"
        )
    return reasons


def proof_is_complete(proof: ProofPackage) -> bool:
    """Structural completeness for a passing proof (Phase 3 gate metric)."""
    if proof.verifier_status != VerifierStatus.PASS:
        return False
    return bool(
        proof.proof_id
        and proof.case_id
        and proof.hypothesis_id
        and proof.evidence_ids
        and proof.supported_evidence_ids
        and proof.equations
        and all(equation.holds for equation in proof.equations)
        and len(proof.canonical_hash) == 64
        and proof.proposed_delta_paise is not None
    )
