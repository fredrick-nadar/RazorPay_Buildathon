"""Google Gemini backend (generativelanguage REST, JSON mode)."""

from __future__ import annotations

from typing import Any

from app.ai.base import (
    DEFAULT_ATTEMPT_TIMEOUT_S,
    LLMError,
    LLMResponse,
    Transport,
    post_json,
    urllib_transport,
)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiBackend:
    """Google Gemini via the generativelanguage REST API."""

    provider_id = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        transport: Transport | None = None,
        timeout_s: float = DEFAULT_ATTEMPT_TIMEOUT_S,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        # The transport is timeout-agnostic; each attempt supplies its own.
        self.transport = transport or urllib_transport
        self.timeout_s = timeout_s

    def chat(
        self,
        system: str,
        user: str,
        json_mode: bool = False,
        timeout_s: float | None = None,
    ) -> LLMResponse:
        url = f"{self.base_url}/models/{self.model}:generateContent"
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.1},
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        parsed = post_json(
            self.transport,
            self.provider_id,
            url,
            {"x-goog-api-key": self.api_key},
            payload,
            self.timeout_s if timeout_s is None else timeout_s,
        )
        try:
            text = str(parsed["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(self.provider_id, f"unexpected response shape: {exc}") from exc
        return LLMResponse(
            text=text,
            provider_id=self.provider_id,
            model=self.model,
            latency_ms=0.0,
        )


__all__ = ["GeminiBackend"]
