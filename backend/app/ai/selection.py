"""Central, truthful investigator selection.

Fake investigation is never an implicit fallback for a requested live run.  A
missing live provider is an explicit availability error, while rules-only and
fake modes remain available for offline operation and reproducible evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.ai.base import Transport
from app.ai.chain import build_chain
from app.ai.policy import InvestigatorExecutionPolicy, policy_from_settings
from app.config import Settings
from app.investigator.budgets import InvestigationBudget
from app.investigator.llm_provider import LLMInvestigatorProvider
from app.investigator.provider import FakeProvider, InvestigatorProvider


class InvestigatorUnavailableError(RuntimeError):
    """The requested investigator cannot be constructed from current settings."""


@dataclass(frozen=True)
class InvestigatorSelection:
    requested: str
    execution_mode: Literal["rules-only", "agent"]
    provider: InvestigatorProvider | None
    simulated: bool
    policy: InvestigatorExecutionPolicy | None = None

    @property
    def provider_id(self) -> str:
        return self.provider.provider_id if self.provider is not None else "none"

    @property
    def policy_fingerprint(self) -> str:
        """Non-secret execution-policy identity, for job/run idempotency."""
        if self.provider is None:
            return "rules-only"
        return str(getattr(self.provider, "policy_fingerprint", "policy-unversioned"))


def resolve_investigator(
    settings: Settings,
    requested: str | None = None,
    *,
    transport: Transport | None = None,
) -> InvestigatorSelection:
    """Resolve a rules-only, explicit fake, or configured live investigator.

    ``agent``/``live`` mean "use the configured chain".  A concrete provider
    name restricts the chain to that provider.  The deterministic fake is used
    only when either the request or ``ARGUS_AI_PROVIDER`` explicitly says fake.
    """

    request_value = (requested or "agent").strip().lower()
    if request_value in {"rules-only", "none"}:
        return InvestigatorSelection(request_value, "rules-only", None, False)
    if request_value in {"fake", "fake-deterministic-v1"}:
        return InvestigatorSelection(request_value, "agent", FakeProvider(), True)

    configured = settings.ai_provider.lower()
    choice = configured if request_value in {"agent", "live", "auto"} else request_value
    if choice == "fake":
        return InvestigatorSelection(request_value, "agent", FakeProvider(), True)
    if choice == "none":
        raise InvestigatorUnavailableError(
            "AI investigation is disabled. Select rules-only mode or configure a live provider."
        )
    if choice not in {"auto", "groq", "gemini", "openai", "sarvam", "ollama"}:
        raise InvestigatorUnavailableError(f"unknown investigator provider {choice!r}")

    selected_settings = (
        settings if choice == configured else settings.model_copy(update={"ai_provider": choice})
    )
    # ONE effective policy, shared by the chain, the budget and every deadline.
    policy = policy_from_settings(selected_settings)
    chain = build_chain(selected_settings, transport=transport, policy=policy)
    if not chain.member_ids:
        hint = (
            "Set ARGUS_GROQ_API_KEY and ARGUS_AI_PROVIDER=groq, select rules-only, "
            "or explicitly select fake for an offline synthetic evaluation."
        )
        raise InvestigatorUnavailableError(f"no live {choice} investigator is configured. {hint}")
    return InvestigatorSelection(
        requested=request_value,
        execution_mode="agent",
        provider=LLMInvestigatorProvider(
            chain,
            budget_config=InvestigationBudget(
                max_tool_calls=policy.tool_call_budget,
                remaining_tool_calls=policy.tool_call_budget,
                max_total_attempts=policy.max_schema_attempts,
                remaining_attempts=policy.max_schema_attempts,
                timeout_s=policy.case_deadline_s,
            ),
            policy=policy,
        ),
        simulated=False,
        policy=policy,
    )


__all__ = [
    "InvestigatorSelection",
    "InvestigatorUnavailableError",
    "resolve_investigator",
]
