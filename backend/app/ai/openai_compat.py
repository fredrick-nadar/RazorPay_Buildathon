"""OpenAI-compatible chat backend.

One implementation covers three named providers because they all speak the
same wire format (POST {base}/chat/completions, Bearer auth):

- OpenAI      : https://api.openai.com/v1          (gpt-4o-mini)
- Sarvam-M    : https://api.sarvam.ai/v1           (sarvam-m) - Indian stack
- Ollama      : http://127.0.0.1:11434/v1          (llama3.1:8b, local Llama)
"""

from __future__ import annotations

from typing import Any

from app.ai.base import LLMError, LLMResponse, Transport, post_json, urllib_transport


class OpenAICompatBackend:
    """Any OpenAI-compatible /chat/completions endpoint."""

    def __init__(
        self,
        provider_id: str,
        api_key: str,
        model: str,
        base_url: str,
        transport: Transport | None = None,
        timeout_s: float = 45.0,
    ) -> None:
        self.provider_id = provider_id
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.transport = transport or urllib_transport
        self.timeout_s = timeout_s

    def chat(self, system: str, user: str, json_mode: bool = False) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "api-subscription-key": self.api_key,
        }
        parsed = post_json(
            self.transport,
            self.provider_id,
            f"{self.base_url}/chat/completions",
            headers,
            payload,
        )
        try:
            text = str(parsed["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(self.provider_id, f"unexpected response shape: {exc}") from exc
        return LLMResponse(
            text=text,
            provider_id=self.provider_id,
            model=self.model,
            latency_ms=0.0,
        )


__all__ = ["OpenAICompatBackend"]
