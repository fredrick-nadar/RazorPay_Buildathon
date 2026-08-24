"""Pydantic v2 validation schemas for untrusted provider output (PRD 10.3).

All models use ``extra="forbid"`` so that any unexpected field — including
``confidence``, ``score``, ``probability``, or ``status_override`` — is
structurally rejected at parse time rather than silently ignored.

Provider-supplied reason codes are constrained to the existing ``ReasonCode``
vocabulary (mapped through ``PROVIDER_REASON_PREFIX``) so persisted UNRESOLVED
summaries stay machine-parseable.  Unknown provider free-text reason codes are
namespaced as ``PROVIDER:<original>`` and never masquerade as system codes.

Internal contract uses frozen dataclasses (``HypothesisOutput``,
``UnresolvedExplanation``, ``ProviderResult``), converted after validation.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.domain.enums import ExceptionCategory, ReasonCode

PROVIDER_REASON_PREFIX = "PROVIDER:"

# Valid system reason codes that a provider may reference directly.
_VALID_REASON_CODES = frozenset(item.value for item in ReasonCode)


def normalise_reason_code(raw: str) -> str:
    """Map a provider-supplied reason code to the system vocabulary.

    Known ``ReasonCode`` values pass through unchanged.  Unknown values are
    prefixed with ``PROVIDER:`` so they remain machine-parseable without
    masquerading as system codes.
    """
    if raw in _VALID_REASON_CODES:
        return raw
    return f"{PROVIDER_REASON_PREFIX}{raw}"


# ---------------------------------------------------------------------------
# Pydantic validation models (untrusted boundary)
# ---------------------------------------------------------------------------


class CompetingHypothesisModel(BaseModel):
    """One competing explanation the provider acknowledges."""

    model_config = ConfigDict(extra="forbid")
    category: str
    why_possible: str
    test_needed: str


class HypothesisOutputModel(BaseModel):
    """Pydantic gate for untrusted provider hypothesis output.

    ``extra='forbid'`` rejects ``confidence``, ``score``, ``status_override``,
    or any other unknown field at parse time.
    """

    model_config = ConfigDict(extra="forbid")
    category: str
    claim: str
    evidence_ids: list[str]
    competing_hypotheses: list[CompetingHypothesisModel]
    known_uncertainty: list[str]

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        try:
            ExceptionCategory(v)
        except ValueError as exc:
            raise ValueError(f"unknown category: {v!r}") from exc
        return v

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, v: list[str]) -> list[str]:
        if len(v) == 0:
            raise ValueError("evidence_ids must not be empty")
        for eid in v:
            if ":" not in eid:
                raise ValueError(f"evidence_id {eid!r} must be TYPE:record_id format")
        return v

    @field_validator("competing_hypotheses")
    @classmethod
    def validate_competing(
        cls,
        v: list[CompetingHypothesisModel],
    ) -> list[CompetingHypothesisModel]:
        if len(v) == 0:
            raise ValueError("competing_hypotheses must have at least one entry")
        return v


class UnresolvedExplanationModel(BaseModel):
    """Provider determines evidence is insufficient for any hypothesis."""

    model_config = ConfigDict(extra="forbid")
    reason_codes: list[str]
    missing_evidence: list[str]
    next_step: str


class ProviderOutputModel(BaseModel):
    """Top-level provider response.  Exactly one of hypothesis or unresolved."""

    model_config = ConfigDict(extra="forbid")
    hypothesis: HypothesisOutputModel | None = None
    unresolved: UnresolvedExplanationModel | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> ProviderOutputModel:
        has_hyp = self.hypothesis is not None
        has_unr = self.unresolved is not None
        if has_hyp == has_unr:
            raise ValueError("exactly one of 'hypothesis' or 'unresolved' must be set")
        return self


# ---------------------------------------------------------------------------
# Internal contract (frozen dataclasses, after validation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompetingHypothesis:
    category: str
    why_possible: str
    test_needed: str


@dataclass(frozen=True)
class HypothesisOutput:
    """Validated hypothesis from the provider.  The engine (not the model)
    routes this through ``verify_case``."""

    category: ExceptionCategory
    claim: str
    evidence_ids: tuple[str, ...]
    competing_hypotheses: tuple[CompetingHypothesis, ...]
    known_uncertainty: tuple[str, ...]


@dataclass(frozen=True)
class UnresolvedExplanation:
    """Provider says evidence is insufficient — case stays/becomes UNRESOLVED.

    ``reason_codes`` are normalised: system ``ReasonCode`` values pass through;
    unknown values are prefixed ``PROVIDER:``.
    """

    reason_codes: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    next_step: str


@dataclass(frozen=True)
class ProviderResult:
    """Validated result.  Exactly one of hypothesis or unresolved.  Never both."""

    hypothesis: HypothesisOutput | None
    unresolved: UnresolvedExplanation | None
    tool_calls_used: int
    retries_used: int

    def __post_init__(self) -> None:
        has_hyp = self.hypothesis is not None
        has_unr = self.unresolved is not None
        if has_hyp == has_unr:
            raise ValueError("exactly one of hypothesis or unresolved must be set")


def convert_provider_output(
    model: ProviderOutputModel,
    tool_calls_used: int,
    retries_used: int,
) -> ProviderResult:
    """Convert validated Pydantic model to frozen internal dataclass."""
    hypothesis: HypothesisOutput | None = None
    unresolved: UnresolvedExplanation | None = None

    if model.hypothesis is not None:
        h = model.hypothesis
        hypothesis = HypothesisOutput(
            category=ExceptionCategory(h.category),
            claim=h.claim,
            evidence_ids=tuple(h.evidence_ids),
            competing_hypotheses=tuple(
                CompetingHypothesis(
                    category=ch.category,
                    why_possible=ch.why_possible,
                    test_needed=ch.test_needed,
                )
                for ch in h.competing_hypotheses
            ),
            known_uncertainty=tuple(h.known_uncertainty),
        )

    if model.unresolved is not None:
        u = model.unresolved
        unresolved = UnresolvedExplanation(
            reason_codes=tuple(normalise_reason_code(rc) for rc in u.reason_codes),
            missing_evidence=tuple(u.missing_evidence),
            next_step=u.next_step,
        )

    return ProviderResult(
        hypothesis=hypothesis,
        unresolved=unresolved,
        tool_calls_used=tool_calls_used,
        retries_used=retries_used,
    )
