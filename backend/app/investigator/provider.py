"""Investigator provider ABC and FakeProvider (PRD 10, Phase 4).

Phase 4 ships with ``FakeProvider`` only — zero external dependencies, zero
model API calls, zero secrets.  The ``FakeProvider`` exercises the real
``ToolDispatcher``, returning deterministic ``HypothesisOutput`` or
``UnresolvedExplanation`` based on what the tools return.

No live model provider is included in Phase 4.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.domain.enums import ExceptionCategory
from app.investigator.budgets import InvestigationBudget
from app.investigator.schemas import (
    CompetingHypothesis,
    HypothesisOutput,
    ProviderResult,
    UnresolvedExplanation,
)
from app.investigator.tools import ToolDispatcher
from app.reconciliation.detectors import CaseRecord


class InvestigatorProvider(ABC):
    """Provider ABC.  Phase 4 has only FakeProvider.  No live provider."""

    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @abstractmethod
    def investigate(
        self,
        case: CaseRecord,
        tools: ToolDispatcher,
        budget: InvestigationBudget,
        context: dict[str, Any],
    ) -> ProviderResult:
        """Investigate a case using tools and return a structured result.

        The provider may call ``tools.dispatch(name, args)`` up to
        ``budget.remaining_tool_calls`` times.  It must return a
        ``ProviderResult`` with exactly one of ``hypothesis`` or ``unresolved``.
        """
        ...


class FakeProvider(InvestigatorProvider):
    """Deterministic scripted provider for testing.  Zero network, zero secrets.

    Exercises the real ``ToolDispatcher`` against the real
    ``EvidenceSnapshot``.  Returns correct hypotheses for resolvable
    categories and ``UnresolvedExplanation`` for ambiguous cases.

    ``provider_id = "fake-deterministic-v1"``.
    """

    @property
    def provider_id(self) -> str:
        return "fake-deterministic-v1"

    @property
    def policy_fingerprint(self) -> str:
        """Fixed identity: the fake is deterministic and has no live policy.

        Distinct from any live policy fingerprint, so an explicit fake run and
        a live run can never share an idempotency key.
        """
        return "fake-deterministic-v1-policy"

    def investigate(
        self,
        case: CaseRecord,
        tools: ToolDispatcher,
        budget: InvestigationBudget,
        context: dict[str, Any],
    ) -> ProviderResult:
        tool_calls_used = 0

        # Step 1: get_case to read case details
        _case_result = tools.dispatch("get_case", {"case_id": case.case_id})
        tool_calls_used += 1

        # Step 2: get each evidence record
        evidence_ids: list[str] = []
        for item in case.evidence:
            eid = f"{item.record_type}:{item.record_id}"
            evidence_ids.append(eid)
            _record_result = tools.dispatch("get_record", {"record_id": eid})
            tool_calls_used += 1

        # Step 3: build hypothesis or unresolved based on category
        if case.category == ExceptionCategory.AMBIGUOUS_EVIDENCE:
            return ProviderResult(
                hypothesis=None,
                unresolved=UnresolvedExplanation(
                    reason_codes=("NON_UNIQUE_EVIDENCE",),
                    missing_evidence=("additional discriminating records or manual review",),
                    next_step=(
                        "human review required: multiple candidate records match "
                        "and available evidence cannot distinguish them"
                    ),
                ),
                tool_calls_used=tool_calls_used,
                retries_used=0,
            )

        # For resolvable categories: return a hypothesis
        claim = _CATEGORY_CLAIMS.get(case.category, "investigation hypothesis")
        competing = _CATEGORY_COMPETING.get(
            case.category,
            (
                {
                    "category": ExceptionCategory.AMBIGUOUS_EVIDENCE.value,
                    "why_possible": "evidence might be insufficient",
                    "test_needed": "check for additional records",
                },
            ),
        )

        return ProviderResult(
            hypothesis=HypothesisOutput(
                category=case.category,
                claim=claim,
                evidence_ids=tuple(sorted(evidence_ids)),
                competing_hypotheses=tuple(CompetingHypothesis(**ch) for ch in competing),
                known_uncertainty=("verifier must confirm",),
            ),
            unresolved=None,
            tool_calls_used=tool_calls_used,
            retries_used=0,
        )


_CATEGORY_CLAIMS: dict[ExceptionCategory, str] = {
    ExceptionCategory.DUPLICATE_LEDGER_POSTING: (
        "two or more ledger rows post one source-side event; reversing "
        "the extra posting restores the expected balance"
    ),
    ExceptionCategory.MISSING_REFUND_POSTING: (
        "a processed refund has no ledger posting inside the posting window; "
        "adding the signed entry restores the expected balance"
    ),
    ExceptionCategory.SETTLEMENT_TIMING_WINDOW_SHIFT: (
        "the settlement booking belongs to an adjacent accounting window; "
        "attribution shifts, total economic value does not"
    ),
}

_CATEGORY_COMPETING: dict[ExceptionCategory, tuple[dict[str, str], ...]] = {
    ExceptionCategory.DUPLICATE_LEDGER_POSTING: (
        {
            "category": ExceptionCategory.AMBIGUOUS_EVIDENCE.value,
            "why_possible": "the duplicate rows might represent distinct corrections",
            "test_needed": "verify source references are identical",
        },
    ),
    ExceptionCategory.MISSING_REFUND_POSTING: (
        {
            "category": ExceptionCategory.SETTLEMENT_TIMING_WINDOW_SHIFT.value,
            "why_possible": "the refund posting might be in an adjacent window",
            "test_needed": "check posting date against window boundaries",
        },
    ),
    ExceptionCategory.SETTLEMENT_TIMING_WINDOW_SHIFT: (
        {
            "category": ExceptionCategory.MISSING_REFUND_POSTING.value,
            "why_possible": "the settlement might include an unposted refund",
            "test_needed": "check for unmatched refund records",
        },
    ),
}
