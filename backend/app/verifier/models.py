"""Verifier and proof data models (PRD 6.9, 6.10, 9.1).

All evidence identifiers use the canonical ``TYPE:record_id`` form. All money
is signed integer paise. Nothing in this module reads ground-truth label
data: the verifier never receives or produces label-only field names.

``StructuredHypothesis.claim`` is explanatory prose only. The verifier never
parses it, so free-form text can never influence authoritative arithmetic
(PRD Phase 3 stop condition).
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from app.domain.enums import (
    ExceptionCategory,
    HypothesisStatus,
    VerifierStatus,
)


@dataclass(frozen=True)
class Equation:
    """One deterministic check with its concrete arithmetic."""

    label: str
    expression: str
    holds: bool


@dataclass(frozen=True)
class RejectedAlternative:
    """A competing explanation the verifier mechanically falsified."""

    description: str
    reason_codes: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class StructuredHypothesis:
    """Structured, machine-checkable hypothesis (PRD 10.3 subset).

    Phase 3 hypotheses are system-generated from the case itself; the Phase 4
    investigator will propose hypotheses through the same schema and the same
    verifier. There is no confidence field anywhere: model confidence can
    never override a deterministic constraint (PRD 5.17, 9.6).
    """

    hypothesis_id: str
    case_id: str
    category: ExceptionCategory
    claim: str
    evidence_ids: tuple[str, ...]
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    reason_codes: tuple[str, ...] = ()


def hypothesis_id_for(
    case_id: str, category: ExceptionCategory, evidence_ids: tuple[str, ...]
) -> str:
    canonical = "|".join(sorted(evidence_ids))
    digest = sha256(f"{case_id}|{category.value}|{canonical}".encode()).hexdigest()[:12]
    return f"hyp-{digest}"


KNOWN_RECORD_TYPES = frozenset({"PAYMENT", "REFUND", "SETTLEMENT", "BANK_ENTRY", "LEDGER_ENTRY"})


def parse_evidence_id(evidence_id: str) -> tuple[str, str] | None:
    """Split ``TYPE:record_id``; ``None`` when the shape is not canonical."""
    if ":" not in evidence_id:
        return None
    record_type, record_id = evidence_id.split(":", 1)
    if record_type not in KNOWN_RECORD_TYPES or not record_id:
        return None
    return record_type, record_id


@dataclass(frozen=True)
class VerifierResult:
    """Outcome of one deterministic verification (PRD 9.1).

    ``proposed_delta_paise`` is set only on ``PASS`` and is derived entirely
    by code from the cited evidence — never from hypothesis prose.
    """

    status: VerifierStatus
    category: ExceptionCategory
    rule_id: str
    rule_version: str
    reason_codes: tuple[str, ...]
    equations: tuple[Equation, ...]
    supported_evidence_ids: tuple[str, ...]
    conflicting_evidence_ids: tuple[str, ...]
    proposed_delta_paise: int | None
    rejected_alternatives: tuple[RejectedAlternative, ...] = ()
    uncertainty: tuple[str, ...] = ()
    competing_candidates: tuple[str, ...] = ()
    missing_discriminator: str | None = None
    recommended_next_step: str | None = None
