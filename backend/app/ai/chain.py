"""Deadline-aware provider chain with fallback (PRD 10 - provider interface).

Resolution (ARGUS_AI_PROVIDER):
    auto    -> [groq?, gemini?, openai?, sarvam?, ollama] filtered by
               configured keys (Ollama joins only when enabled)
    groq    -> [groq]                  (requires ARGUS_GROQ_API_KEY)
    gemini  -> [gemini]                (requires ARGUS_GEMINI_API_KEY)
    openai  -> [openai]                (requires ARGUS_OPENAI_API_KEY)
    sarvam  -> [sarvam]                (requires ARGUS_SARVAM_API_KEY)
    ollama  -> [ollama]                (local, always available)
    fake    -> []                      (deterministic scripted investigator)
    none    -> []                      (rules-only)

Every attempt is bounded by ``min(per-attempt cap, time left on the absolute
deadline - safety reserve)``, so a stuck provider cannot consume the budget its
fallback needs. The deadline is created once per case and never reset.

Each attempt is recorded as safe structured metadata - provider, model, attempt
number, typed outcome, duration - so the honest set of ATTEMPTED providers
survives even a total failure. ``actual_provider`` means a completed response
was received, which is a strictly stronger claim.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.ai.base import LLMError, LLMResponse, Transport
from app.ai.deadline import Deadline
from app.ai.gemini import GeminiBackend
from app.ai.openai_compat import OpenAICompatBackend
from app.ai.policy import policy_from_settings

AttemptOutcome = Literal[
    "SUCCESS",
    "TIMEOUT",
    "RETRYABLE_HTTP",
    "NON_RETRYABLE_HTTP",
    "MALFORMED_RESPONSE",
    "DEADLINE_EXHAUSTED",
    "TRANSPORT_ERROR",
]

# Base backoff between retries of the same provider. Always clamped to the
# remaining deadline, so it can never push past the case budget.
RETRY_BACKOFF_S = 0.5


# The only outcome produced WITHOUT invoking the transport. It records that a
# provider was considered and then skipped for lack of safe time, which is
# scheduling metadata, not a contact attempt.
REFUSAL_OUTCOMES: frozenset[str] = frozenset({"DEADLINE_EXHAUSTED"})


@dataclass(frozen=True)
class ProviderAttempt:
    """Safe structured record of one provider attempt or refusal.

    Deliberately contains no prompt, no response body, no header, no URL and no
    raw exception text - only typed, non-secret metadata fit for persistence.
    """

    provider_id: str
    model: str
    attempt: int
    outcome: AttemptOutcome
    duration_ms: float
    status_code: int | None = None

    @property
    def contacted(self) -> bool:
        """True when the transport was actually invoked for this record.

        A ``DEADLINE_EXHAUSTED`` record is a refusal decided before dialling,
        so reporting it as a contacted provider would overstate what happened.
        """
        return self.outcome not in REFUSAL_OUTCOMES

    def to_json(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model": self.model,
            "attempt": self.attempt,
            "outcome": self.outcome,
            "duration_ms": round(self.duration_ms, 3),
            "status_code": self.status_code,
            # Makes the distinction explicit in persisted telemetry.
            "contacted": self.contacted,
        }


def _ordered_providers(
    attempts: tuple[ProviderAttempt, ...],
    predicate: Callable[[ProviderAttempt], bool],
) -> tuple[str, ...]:
    """Provider ids matching ``predicate``, in first-seen execution order.

    Execution order is preserved deliberately: sorting would discard which
    provider was tried first, which is exactly what a reviewer needs to see.
    """
    seen: list[str] = []
    for item in attempts:
        if predicate(item) and item.provider_id not in seen:
            seen.append(item.provider_id)
    return tuple(seen)


@dataclass(frozen=True)
class NativeToolRequest:
    """An investigator turn rendered for the official function-calling protocol.

    Carried alongside the legacy ``system``/``user`` rendering rather than
    replacing it: a member that speaks the native protocol receives this, and
    every other member receives exactly the request it received before, so
    non-native providers are untouched.
    """

    messages: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ChatOutcome:
    """A completed model turn plus the full attempt history that produced it."""

    response: LLMResponse
    attempts: tuple[ProviderAttempt, ...]

    @property
    def attempted_providers(self) -> tuple[str, ...]:
        """Providers whose transport was actually invoked, in execution order."""
        return _ordered_providers(self.attempts, lambda item: item.contacted)

    @property
    def considered_providers(self) -> tuple[str, ...]:
        """Every provider the chain reached in its walk, contacted or not."""
        return _ordered_providers(self.attempts, lambda _item: True)

    @property
    def skipped_providers(self) -> tuple[str, ...]:
        """Providers refused before dialling because no safe time remained."""
        contacted = set(self.attempted_providers)
        return _ordered_providers(
            self.attempts,
            lambda item: not item.contacted and item.provider_id not in contacted,
        )

    @property
    def actual_provider(self) -> str:
        return self.response.provider_id


class AIChainError(Exception):
    """Every backend in the chain failed.

    Carries the attempt history so a caller can report which providers were
    attempted even though none returned a response.
    """

    def __init__(self, reason: str, attempts: tuple[ProviderAttempt, ...] = ()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.attempts = attempts

    @property
    def attempted_providers(self) -> tuple[str, ...]:
        """Providers whose transport was actually invoked, in execution order."""
        return _ordered_providers(self.attempts, lambda item: item.contacted)

    @property
    def considered_providers(self) -> tuple[str, ...]:
        return _ordered_providers(self.attempts, lambda _item: True)

    @property
    def skipped_providers(self) -> tuple[str, ...]:
        contacted = set(self.attempted_providers)
        return _ordered_providers(
            self.attempts,
            lambda item: not item.contacted and item.provider_id not in contacted,
        )


def _classify(exc: LLMError) -> tuple[AttemptOutcome, bool]:
    """Map a backend error to a typed outcome and whether a retry is allowed."""
    if exc.timeout:
        return "TIMEOUT", True
    if exc.status_code is not None:
        if exc.retryable:
            return "RETRYABLE_HTTP", True
        return "NON_RETRYABLE_HTTP", False
    if "malformed" in exc.reason or "unexpected response shape" in exc.reason:
        return "MALFORMED_RESPONSE", False
    return "TRANSPORT_ERROR", exc.retryable


class AIChain:
    """Ordered backend list with first-success-wins semantics."""

    def __init__(
        self,
        members: list[Any],
        transport: Transport | None = None,
        max_attempts_per_provider: int = 1,
        attempt_timeout_cap_s: float = 11.0,
        safety_reserve_s: float = 0.75,
        min_attempt_s: float = 1.5,
        fallback_reserve_s: float = 0.0,
    ) -> None:
        self.members = members
        self.transport = transport
        self.max_attempts_per_provider = max_attempts_per_provider
        self.attempt_timeout_cap_s = attempt_timeout_cap_s
        self.safety_reserve_s = safety_reserve_s
        self.min_attempt_s = min_attempt_s
        self.fallback_reserve_s = fallback_reserve_s

    @property
    def member_ids(self) -> list[str]:
        return [member.provider_id for member in self.members]

    @property
    def member_models(self) -> list[tuple[str, str]]:
        return [(member.provider_id, str(getattr(member, "model", ""))) for member in self.members]

    def chat_with_attempts(
        self,
        system: str,
        user: str,
        json_mode: bool = False,
        *,
        deadline: Deadline | None = None,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        native: NativeToolRequest | None = None,
    ) -> ChatOutcome:
        """Walk the chain inside one absolute deadline, recording every attempt."""
        if not self.members:
            raise AIChainError("no AI backend configured (rules-only mode)")
        clock = now or time.monotonic
        pause = sleep if sleep is not None else time.sleep
        window = deadline or Deadline.after(
            self.attempt_timeout_cap_s * max(1, len(self.members)),
            safety_reserve_s=self.safety_reserve_s,
            now=clock(),
            min_attempt_s=self.min_attempt_s,
        )
        attempts: list[ProviderAttempt] = []

        for position, member in enumerate(self.members):
            model = str(getattr(member, "model", ""))
            # Hold back a usable attempt for whatever follows this provider.
            # The LAST provider reserves nothing, so it can use all that is
            # left rather than stranding time nobody can spend.
            has_fallback = position + 1 < len(self.members)
            reserve_extra = self.fallback_reserve_s if has_fallback else 0.0
            for attempt_index in range(1, self.max_attempts_per_provider + 1):
                budget = window.attempt_timeout(
                    self.attempt_timeout_cap_s,
                    now=clock(),
                    reserve_extra_s=reserve_extra,
                )
                if budget is None:
                    # No safe time left: record the refusal rather than starting
                    # an attempt the watchdog would preempt.
                    attempts.append(
                        ProviderAttempt(
                            provider_id=member.provider_id,
                            model=model,
                            attempt=attempt_index,
                            outcome="DEADLINE_EXHAUSTED",
                            duration_ms=0.0,
                        )
                    )
                    break

                started = clock()
                try:
                    if native is not None and getattr(member, "supports_native_tools", False):
                        response: LLMResponse = member.chat(
                            system,
                            user,
                            timeout_s=budget,
                            messages=list(native.messages),
                            tools=list(native.tools),
                        )
                    else:
                        response = member.chat(system, user, json_mode=json_mode, timeout_s=budget)
                except LLMError as exc:
                    outcome, retryable = _classify(exc)
                    attempts.append(
                        ProviderAttempt(
                            provider_id=member.provider_id,
                            model=model,
                            attempt=attempt_index,
                            outcome=outcome,
                            duration_ms=(clock() - started) * 1000.0,
                            status_code=exc.status_code,
                        )
                    )
                    if not retryable or attempt_index >= self.max_attempts_per_provider:
                        break
                    # The backoff is part of this provider's allocation, so it
                    # must respect the fallback reservation too.
                    delay = window.sleep_budget(
                        RETRY_BACKOFF_S, now=clock(), reserve_extra_s=reserve_extra
                    )
                    if delay > 0:
                        pause(delay)
                    continue
                attempts.append(
                    ProviderAttempt(
                        provider_id=member.provider_id,
                        model=model,
                        attempt=attempt_index,
                        outcome="SUCCESS",
                        duration_ms=(clock() - started) * 1000.0,
                        status_code=200,
                    )
                )
                return ChatOutcome(response=response, attempts=tuple(attempts))

        raise AIChainError(
            "all configured AI backends failed within the case deadline",
            tuple(attempts),
        )

    def chat(
        self,
        system: str,
        user: str,
        json_mode: bool = False,
        *,
        deadline: Deadline | None = None,
    ) -> LLMResponse:
        """Bounded single-response entry point for non-investigator callers."""
        return self.chat_with_attempts(
            system, user, json_mode=json_mode, deadline=deadline
        ).response


def build_chain(
    settings: Any,
    transport: Transport | None = None,
    policy: Any | None = None,
) -> AIChain:
    """Resolve the backend chain using ONE effective execution policy.

    ``policy`` is the already-clamped :class:`InvestigatorExecutionPolicy`. When
    omitted it is derived here, so a caller can never mix raw ``Settings``
    values with clamped policy values - which is how a configured minimum
    attempt failed to reach the runtime deadline (REVIEW-012).
    """

    def key(setting: Any) -> str | None:
        value = setting.get_secret_value() if setting is not None else None
        return value or None

    effective = policy if policy is not None else policy_from_settings(settings)
    attempt_cap = float(effective.attempt_timeout_cap_s)

    def gemini() -> Any | None:
        api_key = key(settings.gemini_api_key)
        if not api_key:
            return None
        return GeminiBackend(
            api_key=api_key,
            model=settings.gemini_model,
            base_url=settings.gemini_base_url,
            transport=transport,
            timeout_s=attempt_cap,
        )

    def compat(provider_id: str, api_key: str, model: str, base_url: str) -> Any:
        return OpenAICompatBackend(
            provider_id=provider_id,
            api_key=api_key,
            model=model,
            base_url=base_url,
            transport=transport,
            timeout_s=attempt_cap,
        )

    def openai() -> Any | None:
        api_key = key(settings.openai_api_key)
        if not api_key:
            return None
        return compat("openai", api_key, settings.openai_model, settings.openai_base_url)

    def groq() -> Any | None:
        api_key = key(settings.groq_api_key)
        if not api_key:
            return None
        return compat(
            "groq",
            api_key,
            settings.groq_investigator_model,
            settings.groq_base_url,
        )

    def sarvam() -> Any | None:
        api_key = key(settings.sarvam_api_key)
        if not api_key:
            return None
        return compat("sarvam", api_key, settings.sarvam_model, settings.sarvam_base_url)

    def ollama() -> Any:
        return compat(
            "ollama",
            settings.ollama_api_key,
            settings.ollama_model,
            settings.ollama_base_url,
        )

    choice = settings.ai_provider.lower()
    if choice == "groq":
        members = [m for m in [groq()] if m is not None]
    elif choice == "gemini":
        members = [m for m in [gemini()] if m is not None]
    elif choice == "openai":
        members = [m for m in [openai()] if m is not None]
    elif choice == "sarvam":
        members = [m for m in [sarvam()] if m is not None]
    elif choice == "ollama":
        members = [ollama()]
    elif choice in ("fake", "none"):
        members = []
    else:  # auto: cloud keys first; local Llama only when enabled
        members = []
        for candidate in (groq(), gemini(), openai(), sarvam()):
            if candidate is not None:
                members.append(candidate)
        if settings.ollama_enabled:
            members.append(ollama())
    return AIChain(
        members,
        transport=transport,
        # Every knob comes from the one effective policy.
        max_attempts_per_provider=effective.max_attempts_per_provider,
        attempt_timeout_cap_s=attempt_cap,
        safety_reserve_s=float(effective.safety_reserve_s),
        min_attempt_s=float(effective.min_attempt_s),
        fallback_reserve_s=float(effective.fallback_reserve_s),
    )


__all__ = [
    "AIChain",
    "AIChainError",
    "ChatOutcome",
    "NativeToolRequest",
    "ProviderAttempt",
    "build_chain",
]
