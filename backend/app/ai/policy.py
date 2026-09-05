"""Versioned, non-secret investigator execution policy and its fingerprint.

Two jobs:

1. Hold every deadline, retry and budget value in one configurable place, and
   guarantee the timeout hierarchy cannot be inverted by configuration:
   per-attempt cap <= turn window <= case deadline.

2. Produce a stable non-secret fingerprint over the things that materially
   change what an investigation does - ordered provider and model identities,
   prompt/tool/result protocol versions, deadlines, retry bound and tool
   budget. Agent job and run idempotency include it so a corrected timeout
   policy cannot reuse an older timed-out result.

API keys are never read, hashed, serialized or described here. Only provider
IDs and model IDs, both of which are non-secret configuration, take part.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

# Bump when the system prompt or turn protocol changes shape.
PROMPT_PROTOCOL_VERSION = "investigator-prompt-v5"
# Bump when the tool catalogue, names or argument contract change.
TOOL_PROTOCOL_VERSION = "investigator-tools-v5"
# Bump when the accepted final-result schema changes.
RESULT_SCHEMA_VERSION = "provider-output-v2"
# Bump when the PROVIDER WIRE REQUEST changes shape - the fields ARGUS puts
# on the HTTP request itself, independent of the prompt, the tool catalogue
# and the result schema. v2 added Groq's required JSON-mode
# ``reasoning_format: "hidden"``; v3 replaces the prompt-simulated tool
# envelope with Groq's official function-calling protocol - tools,
# tool_choice, parallel_tool_calls and role-tool history
# (cloud-reference sections 42 and 44).
PROVIDER_REQUEST_PROTOCOL_VERSION = "provider-request-v4"

# Defaults chosen so a real Groq turn fits comfortably while a stuck attempt
# is abandoned early enough to leave fallback time inside the same case budget.
DEFAULT_ATTEMPT_CAP_S = 11.0
DEFAULT_TURN_WINDOW_S = 25.0
DEFAULT_CASE_DEADLINE_S = 75.0
DEFAULT_SAFETY_RESERVE_S = 0.75
DEFAULT_MIN_ATTEMPT_S = 1.5
# Grace given to the outer watchdog over the case deadline. It exists only to
# catch a broken custom provider that ignores the deadline entirely.
DEFAULT_WATCHDOG_GRACE_S = 5.0


@dataclass(frozen=True)
class InvestigatorExecutionPolicy:
    """Everything that decides how an investigation is executed."""

    providers: tuple[tuple[str, str], ...]
    attempt_timeout_cap_s: float
    turn_deadline_s: float
    case_deadline_s: float
    safety_reserve_s: float
    min_attempt_s: float
    # Wall time held back for the providers AFTER the current one, so a first
    # provider cannot consume the window its fallback needs (REVIEW-006).
    fallback_reserve_s: float
    watchdog_grace_s: float
    max_attempts_per_provider: int
    tool_call_budget: int
    max_schema_attempts: int
    require_tool_call_before_final: bool
    prompt_protocol_version: str
    tool_protocol_version: str
    result_schema_version: str
    provider_request_protocol_version: str

    def __post_init__(self) -> None:
        if self.attempt_timeout_cap_s > self.turn_deadline_s:
            raise ValueError("attempt cap must not exceed the turn window")
        if self.turn_deadline_s > self.case_deadline_s:
            raise ValueError("turn window must not exceed the case deadline")
        if self.min_attempt_s > self.attempt_timeout_cap_s:
            raise ValueError("minimum attempt must not exceed the attempt cap")
        # The reserve is withheld from every attempt, so a reserve that eats the
        # whole turn makes every provider impossible to call.
        if self.safety_reserve_s + self.min_attempt_s > self.turn_deadline_s:
            raise ValueError(
                "safety reserve plus minimum attempt must leave usable time in the turn"
            )
        if self.fallback_reserve_s < 0:
            raise ValueError("fallback reserve must not be negative")

    @property
    def watchdog_timeout_s(self) -> float:
        """Last-resort worker timeout: the case deadline plus a small grace."""
        return self.case_deadline_s + self.watchdog_grace_s

    def as_kwargs(self) -> dict[str, Any]:
        return dict(asdict(self))

    def describe(self) -> dict[str, Any]:
        """Non-secret description; safe to persist and to show in the UI."""
        return {
            "providers": [
                {"provider_id": provider, "model": model} for provider, model in self.providers
            ],
            "attempt_timeout_cap_s": self.attempt_timeout_cap_s,
            "turn_deadline_s": self.turn_deadline_s,
            "case_deadline_s": self.case_deadline_s,
            "safety_reserve_s": self.safety_reserve_s,
            "min_attempt_s": self.min_attempt_s,
            "fallback_reserve_s": self.fallback_reserve_s,
            # Changes the effective watchdog and therefore queued-job execution
            # behaviour, so it is part of policy identity (REVIEW-008).
            "watchdog_grace_s": self.watchdog_grace_s,
            "max_attempts_per_provider": self.max_attempts_per_provider,
            "tool_call_budget": self.tool_call_budget,
            "max_schema_attempts": self.max_schema_attempts,
            "require_tool_call_before_final": self.require_tool_call_before_final,
            "prompt_protocol_version": self.prompt_protocol_version,
            "tool_protocol_version": self.tool_protocol_version,
            "result_schema_version": self.result_schema_version,
            # The wire request itself is part of execution identity: a run
            # made under a rejected request shape must not be reused.
            "provider_request_protocol_version": self.provider_request_protocol_version,
        }

    def fingerprint(self) -> str:
        """Stable SHA-256 over the non-secret description."""
        material = json.dumps(
            # v2 added watchdog_grace_s; v3 adds
            # provider_request_protocol_version. Bumping the version
            # guarantees a job queued under an older identity can never be
            # executed under a newer one: the request key differs, so the
            # worker refuses.
            {"version": "investigator-policy-v3", **self.describe()},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _configured_providers(settings: Any) -> tuple[tuple[str, str], ...]:
    """Ordered (provider_id, model) pairs the chain would actually build.

    Mirrors ``build_chain`` ordering. Only presence of a key is consulted, never
    its value, so the policy identity is independent of credential rotation.
    """

    def has(name: str) -> bool:
        value = getattr(settings, name, None)
        if value is None:
            return False
        secret = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
        return bool(secret and secret.strip())

    catalogue: dict[str, tuple[bool, str]] = {
        "groq": (has("groq_api_key"), settings.groq_investigator_model),
        "gemini": (has("gemini_api_key"), settings.gemini_model),
        "openai": (has("openai_api_key"), settings.openai_model),
        "sarvam": (has("sarvam_api_key"), settings.sarvam_model),
        "ollama": (bool(settings.ollama_enabled), settings.ollama_model),
    }
    choice = str(settings.ai_provider).lower()
    if choice in {"fake", "none"}:
        return ()
    if choice in catalogue:
        available, model = catalogue[choice]
        # An explicitly selected provider is part of the policy even when its
        # key is absent; selection raises separately and the identity is stable.
        if choice == "ollama" or available:
            return ((choice, model),)
        return ((choice, model),)
    ordered: list[tuple[str, str]] = []
    for provider in ("groq", "gemini", "openai", "sarvam"):
        available, model = catalogue[provider]
        if available:
            ordered.append((provider, model))
    if settings.ollama_enabled:
        ordered.append(("ollama", catalogue["ollama"][1]))
    return tuple(ordered)


def policy_from_settings(settings: Any) -> InvestigatorExecutionPolicy:
    """Build the policy, clamping any configuration that inverts the hierarchy.

    Configuration is honoured wherever it is coherent. An inverted hierarchy is
    clamped rather than obeyed, because obeying it reproduces the live failure:
    a per-attempt timeout larger than the case budget guarantees that the
    watchdog preempts every attempt.
    """

    # Read each value with an explicit presence check. Never `value or default`:
    # zero is a legal configured value for the reserve and the grace, and
    # truthiness silently replaced it (REVIEW-010).
    def setting(name: str, fallback: float) -> float:
        value = getattr(settings, name, None)
        return fallback if value is None else float(value)

    case_deadline_s = setting("investigator_timeout_s", DEFAULT_CASE_DEADLINE_S)
    turn_deadline_s = setting("investigator_turn_timeout_s", DEFAULT_TURN_WINDOW_S)
    attempt_cap_s = setting("ai_timeout_s", DEFAULT_ATTEMPT_CAP_S)
    safety_reserve_s = setting("investigator_safety_reserve_s", DEFAULT_SAFETY_RESERVE_S)
    watchdog_grace_s = setting("investigator_watchdog_grace_s", DEFAULT_WATCHDOG_GRACE_S)
    min_attempt_s = setting("investigator_min_attempt_s", DEFAULT_MIN_ATTEMPT_S)

    # ONE documented clamp rule: windows are only ever shortened, never
    # lengthened, so an operator can always tighten a deadline but can never
    # invert the hierarchy (which is what preempted every provider live).
    turn_deadline_s = min(turn_deadline_s, case_deadline_s)
    attempt_cap_s = min(attempt_cap_s, turn_deadline_s)
    # The minimum-attempt invariant must be REAL: it is meaningless to refuse an
    # attempt for being under a minimum the cap itself cannot reach.
    min_attempt_s = min(min_attempt_s, attempt_cap_s)

    # Reserve one full attempt for whatever follows the current provider. A
    # larger reserve than the turn can hold would block the FIRST provider, so
    # it is clamped to leave a viable first attempt.
    fallback_reserve_s = setting("investigator_fallback_reserve_s", attempt_cap_s)
    fallback_reserve_s = max(
        0.0,
        min(fallback_reserve_s, turn_deadline_s - safety_reserve_s - min_attempt_s),
    )

    return InvestigatorExecutionPolicy(
        providers=_configured_providers(settings),
        attempt_timeout_cap_s=attempt_cap_s,
        turn_deadline_s=turn_deadline_s,
        case_deadline_s=case_deadline_s,
        safety_reserve_s=safety_reserve_s,
        min_attempt_s=min_attempt_s,
        fallback_reserve_s=fallback_reserve_s,
        watchdog_grace_s=watchdog_grace_s,
        max_attempts_per_provider=int(settings.ai_provider_max_attempts),
        tool_call_budget=int(settings.investigator_tool_budget),
        max_schema_attempts=int(settings.investigator_max_retries),
        require_tool_call_before_final=bool(
            getattr(settings, "investigator_require_tool_call", True)
        ),
        prompt_protocol_version=PROMPT_PROTOCOL_VERSION,
        tool_protocol_version=TOOL_PROTOCOL_VERSION,
        result_schema_version=RESULT_SCHEMA_VERSION,
        provider_request_protocol_version=PROVIDER_REQUEST_PROTOCOL_VERSION,
    )


__all__ = [
    "DEFAULT_ATTEMPT_CAP_S",
    "DEFAULT_CASE_DEADLINE_S",
    "DEFAULT_TURN_WINDOW_S",
    "InvestigatorExecutionPolicy",
    "PROMPT_PROTOCOL_VERSION",
    "PROVIDER_REQUEST_PROTOCOL_VERSION",
    "RESULT_SCHEMA_VERSION",
    "TOOL_PROTOCOL_VERSION",
    "policy_from_settings",
]
