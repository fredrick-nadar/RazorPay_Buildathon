"""Safe structured investigator-execution failure (REVIEW-007).

A bounded investigation can fail AFTER real work has happened: providers may
have returned completed responses, the model may have retried, and evidence
tools may have been called. Raising a plain ``ValueError`` threw all of that
away, so a failed case reported ``actual_providers=[]``,
``attempted_providers=[]``, ``total_retries=0`` and ``total_tool_calls=0`` even
though the model had answered twice.

``InvestigatorExecutionError`` carries that partial work in a form fit for
persistence. It deliberately holds ONLY typed, non-secret metadata: no prompt,
no response body, no header, no URL, no credential, no record prose and no raw
exception string. ``actual_providers`` is derived from attempts whose outcome is
``SUCCESS``, so the claim "an empty actual set means no provider answered"
becomes true on the failure path too.
"""

from __future__ import annotations

from typing import Any, Literal

InvestigationFailureCode = Literal[
    # No backend completed a model turn inside the case deadline.
    "PROVIDER_CHAIN_EXHAUSTED",
    # The absolute case deadline passed between turns.
    "CASE_DEADLINE_EXHAUSTED",
    # The model kept returning text that is not one JSON object.
    "MALFORMED_MODEL_JSON",
    # The final object failed the strict result schema.
    "INVALID_FINAL_SCHEMA",
    # A final verdict arrived without any case-bound evidence tool call.
    "FINAL_WITHOUT_CASE_EVIDENCE",
    # The model kept sending an action that is neither tool nor final.
    "UNKNOWN_MODEL_ACTION",
    # The model still wanted tools after the tool-call budget ran out.
    "TOOL_BUDGET_EXHAUSTED",
    # A live investigator was constructed with no configured backend.
    "NO_PROVIDER_CONFIGURED",
    # --- native function-calling protocol failures (REVIEW-017) -----------
    # The model asked for more than one tool in a single turn.
    # A tool call arrived without a usable id or name.
    "MALFORMED_TOOL_CALL",
    # The requested tool is not in the canonical contract.
    "UNKNOWN_TOOL_REQUESTED",
    # The arguments were not a JSON object matching the tool contract.
    "INVALID_TOOL_ARGUMENTS",
    # A tool result could not be tied back to the call that produced it.
    "TOOL_CALL_ID_MISMATCH",
]


class InvestigatorExecutionError(Exception):
    """A controlled investigation failure that preserves safe partial work."""

    def __init__(
        self,
        code: InvestigationFailureCode,
        *,
        attempts: tuple[dict[str, Any], ...] = (),
        trace: tuple[dict[str, Any], ...] = (),
        retries_used: int = 0,
        tool_calls_used: int = 0,
        evidence_tool_calls: int = 0,
    ) -> None:
        # The message is the typed code and bounded counters only. It is safe
        # to log and to persist; it never carries model or record content.
        super().__init__(
            f"{code} (attempts={len(attempts)}, retries={retries_used}, "
            f"tool_calls={tool_calls_used}, evidence_calls={evidence_tool_calls})"
        )
        self.code = code
        self.attempts = attempts
        self.trace = trace
        self.retries_used = retries_used
        self.tool_calls_used = tool_calls_used
        self.evidence_tool_calls = evidence_tool_calls

    def _ordered(self, predicate: Any) -> tuple[str, ...]:
        """Provider ids matching ``predicate``, in first-seen execution order."""
        seen: list[str] = []
        for item in self.attempts:
            provider = str(item.get("provider_id") or "")
            if provider and predicate(item) and provider not in seen:
                seen.append(provider)
        return tuple(seen)

    @property
    def attempted_providers(self) -> tuple[str, ...]:
        """Providers whose transport was actually INVOKED (REVIEW-011).

        A provider considered and then skipped for lack of safe time carries
        ``contacted=False`` and is reported under ``skipped_providers``.
        """
        return self._ordered(lambda item: bool(item.get("contacted", True)))

    @property
    def considered_providers(self) -> tuple[str, ...]:
        """Every provider the chain reached, contacted or not."""
        return self._ordered(lambda _item: True)

    @property
    def skipped_providers(self) -> tuple[str, ...]:
        contacted = set(self.attempted_providers)
        return self._ordered(
            lambda item: (
                not bool(item.get("contacted", True))
                and str(item.get("provider_id") or "") not in contacted
            )
        )

    @property
    def actual_providers(self) -> tuple[str, ...]:
        """Providers that RETURNED a completed response, in first-seen order.

        Derived from ``SUCCESS`` attempt outcomes, so it stays correct when the
        overall case fails after one or more completed model turns.
        """
        return self._ordered(lambda item: item.get("outcome") == "SUCCESS")

    def telemetry(self) -> dict[str, Any]:
        """Safe structured summary for the engine to persist."""
        return {
            "failure_code": self.code,
            "attempts": list(self.attempts),
            "attempted_providers": list(self.attempted_providers),
            "considered_providers": list(self.considered_providers),
            "skipped_providers": list(self.skipped_providers),
            "actual_providers": list(self.actual_providers),
            "retries_used": self.retries_used,
            "tool_calls_used": self.tool_calls_used,
            "evidence_tool_calls": self.evidence_tool_calls,
            "trace": list(self.trace),
        }


__all__ = ["InvestigationFailureCode", "InvestigatorExecutionError"]
