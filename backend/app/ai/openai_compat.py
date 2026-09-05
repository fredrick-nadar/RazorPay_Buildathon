"""OpenAI-compatible chat backend.

One implementation covers three named providers because they all speak the
same wire format (POST {base}/chat/completions, Bearer auth):

- OpenAI      : https://api.openai.com/v1          (gpt-4o-mini)
- Sarvam-M    : https://api.sarvam.ai/v1           (sarvam-m) - Indian stack
- Ollama      : http://127.0.0.1:11434/v1          (llama3.1:8b, local Llama)
"""

from __future__ import annotations

from typing import Any

from app.ai.base import (
    DEFAULT_ATTEMPT_TIMEOUT_S,
    LLMError,
    LLMResponse,
    ToolCall,
    Transport,
    post_json,
    urllib_transport,
)

# Groq's reasoning models (the GPT-OSS family) default to reasoning_format
# "raw", which Groq rejects in JSON mode - the measured failure was
# 400 invalid_request_error / tool_use_failed (cloud-reference section 41).
# Groq accepts "parsed" or "hidden"; ARGUS uses "hidden" because model
# reasoning must never be exposed, parsed, logged or persisted. This is a
# Groq-only wire requirement, so it is applied only to that provider.
GROQ_PROVIDER_ID = "groq"
GROQ_JSON_REASONING_FORMAT = "hidden"

# Providers whose native function-calling protocol ARGUS speaks. Native tool
# mode is entered ONLY by passing tools explicitly - never inferred from
# prompt text or from json_mode - and only these providers may enter it.
NATIVE_TOOL_PROVIDERS: frozenset[str] = frozenset({GROQ_PROVIDER_ID})


class OpenAICompatBackend:
    """Any OpenAI-compatible /chat/completions endpoint."""

    def __init__(
        self,
        provider_id: str,
        api_key: str,
        model: str,
        base_url: str,
        transport: Transport | None = None,
        timeout_s: float = DEFAULT_ATTEMPT_TIMEOUT_S,
    ) -> None:
        self.provider_id = provider_id
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        # The transport is timeout-agnostic; each attempt supplies its own.
        self.transport = transport or urllib_transport
        self.timeout_s = timeout_s

    @property
    def supports_native_tools(self) -> bool:
        """Whether this provider speaks the official function-calling protocol."""
        return self.provider_id in NATIVE_TOOL_PROVIDERS

    def chat(
        self,
        system: str,
        user: str,
        json_mode: bool = False,
        timeout_s: float | None = None,
        *,
        messages: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """One completion.

        Passing ``tools`` selects INVESTIGATOR NATIVE TOOL MODE explicitly. In
        that mode the request carries the official ``tools`` array,
        ``tool_choice`` and ``parallel_tool_calls``, and deliberately does NOT
        carry ``response_format``: the native protocol replaces the old
        JSON-action envelope rather than being layered on top of it. Without
        ``tools`` every provider keeps its existing behaviour exactly, which is
        what schema mapping and the non-Groq backends rely on.
        """
        native_tool_mode = tools is not None
        if native_tool_mode and not self.supports_native_tools:
            raise LLMError(
                self.provider_id,
                "native tool mode requested for a provider that does not support it",
            )
        turns = (
            list(messages)
            if messages is not None
            else [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": turns,
            "temperature": 0.1,
        }
        if native_tool_mode:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = True
            # Reasoning must never be exposed, parsed, logged or persisted.
            payload["reasoning_format"] = GROQ_JSON_REASONING_FORMAT
        elif json_mode:
            payload["response_format"] = {"type": "json_object"}
            if self.provider_id == GROQ_PROVIDER_ID:
                # Groq-only, JSON-mode-only. Text mode keeps Groq's default,
                # and no other provider is sent this field.
                payload["reasoning_format"] = GROQ_JSON_REASONING_FORMAT
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        if self.provider_id == "sarvam":
            headers["api-subscription-key"] = self.api_key
        parsed = post_json(
            self.transport,
            self.provider_id,
            f"{self.base_url}/chat/completions",
            headers,
            payload,
            self.timeout_s if timeout_s is None else timeout_s,
        )
        try:
            message = parsed["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(self.provider_id, f"unexpected response shape: {exc}") from exc
        if not isinstance(message, dict):
            raise LLMError(self.provider_id, "unexpected response shape: message")

        # ``content`` is legitimately null on a tool-call turn.
        content = message.get("content")
        text = "" if content is None else str(content)
        if not native_tool_mode:
            if content is None:
                raise LLMError(self.provider_id, "unexpected response shape: no content")
            return LLMResponse(
                text=text,
                provider_id=self.provider_id,
                model=self.model,
                latency_ms=0.0,
            )

        # Native tool mode: read ONLY id/name/arguments. Every other field the
        # provider returns - reasoning included - is ignored and never stored.
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise LLMError(self.provider_id, "unexpected response shape: tool_calls")
        calls: list[ToolCall] = []
        for entry in raw_calls:
            if not isinstance(entry, dict):
                raise LLMError(self.provider_id, "unexpected response shape: tool_call entry")
            function = entry.get("function")
            if not isinstance(function, dict):
                raise LLMError(self.provider_id, "unexpected response shape: tool_call function")
            calls.append(
                ToolCall(
                    id=str(entry.get("id") or ""),
                    name=str(function.get("name") or ""),
                    arguments_json=str(function.get("arguments") or ""),
                )
            )
        return LLMResponse(
            text=text,
            provider_id=self.provider_id,
            model=self.model,
            latency_ms=0.0,
            tool_calls=tuple(calls),
            native_tool_protocol=True,
        )


__all__ = [
    "GROQ_JSON_REASONING_FORMAT",
    "GROQ_PROVIDER_ID",
    "NATIVE_TOOL_PROVIDERS",
    "OpenAICompatBackend",
]
