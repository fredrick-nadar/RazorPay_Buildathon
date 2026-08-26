"""Provider chain with automatic fallback (PRD 10 - provider interface).

Resolution (ARGUS_AI_PROVIDER):
    auto    -> [gemini?, openai?, sarvam?, ollama] filtered by configured keys
               (Ollama is local, so it is always a candidate)
    gemini  -> [gemini]                (requires ARGUS_GEMINI_API_KEY)
    openai  -> [openai]                (requires ARGUS_OPENAI_API_KEY)
    sarvam  -> [sarvam]                (requires ARGUS_SARVAM_API_KEY)
    ollama  -> [ollama]                (local, always available)
    fake    -> []                      (deterministic scripted investigator)
    none    -> []                      (rules-only)

``AIChain.chat`` walks the chain in order; the first backend that answers
wins. All backends failing raises :class:`AIChainError` - the investigator
engine converts that into a controlled INVESTIGATION_FAILED state, never a
financial mutation.
"""

from __future__ import annotations

from typing import Any

from app.ai.base import LLMError, LLMResponse, Transport
from app.ai.gemini import GeminiBackend
from app.ai.openai_compat import OpenAICompatBackend


class AIChainError(Exception):
    """Every backend in the chain failed."""


class AIChain:
    """Ordered backend list with first-success-wins semantics."""

    def __init__(self, members: list[Any], transport: Transport | None = None) -> None:
        self.members = members
        if transport is not None:
            # Re-bind transports (test injection).
            self.members = [m for m in members]
        self.transport = transport

    @property
    def member_ids(self) -> list[str]:
        return [member.provider_id for member in self.members]

    def chat(self, system: str, user: str, json_mode: bool = False) -> LLMResponse:
        if not self.members:
            raise AIChainError("no AI backend configured (rules-only mode)")
        errors: list[str] = []
        for member in self.members:
            try:
                response: LLMResponse = member.chat(system, user, json_mode=json_mode)
                return response
            except LLMError as exc:
                errors.append(exc.reason)
        raise AIChainError("all backends failed: " + " | ".join(errors))


def build_chain(settings: Any, transport: Transport | None = None) -> AIChain:
    """Resolve the backend chain from application settings."""

    def key(setting: Any) -> str | None:
        value = setting.get_secret_value() if setting is not None else None
        return value or None

    def gemini() -> Any | None:
        api_key = key(settings.gemini_api_key)
        if not api_key:
            return None
        return GeminiBackend(
            api_key=api_key,
            model=settings.gemini_model,
            base_url=settings.gemini_base_url,
            transport=transport,
            timeout_s=settings.ai_timeout_s,
        )

    def compat(provider_id: str, api_key: str, model: str, base_url: str) -> Any:
        return OpenAICompatBackend(
            provider_id=provider_id,
            api_key=api_key,
            model=model,
            base_url=base_url,
            transport=transport,
            timeout_s=settings.ai_timeout_s,
        )

    def openai() -> Any | None:
        api_key = key(settings.openai_api_key)
        if not api_key:
            return None
        return compat("openai", api_key, settings.openai_model, settings.openai_base_url)

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
    if choice == "gemini":
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
        for candidate in (gemini(), openai(), sarvam()):
            if candidate is not None:
                members.append(candidate)
        if settings.ollama_enabled:
            members.append(ollama())
    return AIChain(members, transport=transport)


__all__ = ["AIChain", "AIChainError", "build_chain"]
