"""Tests for the AI provider chain (PRD 10 provider interface).

All tests use scripted transports - zero network, zero keys required.
"""

from __future__ import annotations

import json
from typing import Any

from app.ai.base import LLMError
from app.ai.chain import AIChain, AIChainError, build_chain
from app.ai.gemini import GeminiBackend
from app.ai.openai_compat import OpenAICompatBackend
from app.config import Settings


def _scripted(responses: list[str | Exception]) -> tuple[Any, list[dict[str, Any]]]:
    """Transport returning scripted bodies in order; records requests."""
    calls: list[dict[str, Any]] = []
    queue = list(responses)

    def transport(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        calls.append({"url": url, "headers": headers, "body": json.loads(body.decode("utf-8"))})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return 200, json.dumps(item).encode("utf-8")

    return transport, calls


class TestChainResolution:
    def test_auto_with_no_keys_is_rules_only(self) -> None:
        chain = build_chain(Settings(_env_file=None, ai_provider="auto"))
        assert chain.member_ids == []

    def test_ollama_enabled_joins_auto_chain(self) -> None:
        chain = build_chain(Settings(_env_file=None, ai_provider="auto", ollama_enabled=True))
        assert chain.member_ids == ["ollama"]

    def test_auto_order_is_gemini_openai_sarvam_then_local(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_provider="auto",
            gemini_api_key="g",
            openai_api_key="o",
            sarvam_api_key="s",
            ollama_enabled=True,
        )
        chain = build_chain(settings)
        assert chain.member_ids == ["gemini", "openai", "sarvam", "ollama"]

    def test_explicit_provider_needs_key(self) -> None:
        chain = build_chain(Settings(_env_file=None, ai_provider="openai"))
        assert chain.member_ids == []
        import pytest

        with pytest.raises(AIChainError):
            chain.chat("sys", "user")

    def test_fake_none_means_empty_chain(self) -> None:
        for choice in ("fake", "none"):
            chain = build_chain(Settings(_env_file=None, ai_provider=choice))
            assert chain.member_ids == []

    def test_ollama_points_to_local(self) -> None:
        chain = build_chain(Settings(ai_provider="ollama"))
        member = chain.members[0]
        assert member.base_url == "http://127.0.0.1:11434/v1"
        assert member.model == "llama3.1:8b"


class TestBackends:
    def test_gemini_request_shape_and_parse(self) -> None:
        transport, calls = _scripted(
            [{"candidates": [{"content": {"parts": [{"text": '{"answer": 42}'}]}}]}]
        )
        backend = GeminiBackend(api_key="k", transport=transport)
        response = backend.chat("system prompt", "user prompt", json_mode=True)
        assert response.text == '{"answer": 42}'
        assert response.provider_id == "gemini"
        call = calls[0]
        assert "generativelanguage" in call["url"]
        assert call["headers"]["x-goog-api-key"] == "k"
        assert call["body"]["generationConfig"]["responseMimeType"] == "application/json"

    def test_openai_compat_sarvam_shape(self) -> None:
        transport, calls = _scripted([{"choices": [{"message": {"content": "ok"}}]}])
        backend = OpenAICompatBackend(
            "sarvam",
            api_key="sk",
            model="sarvam-m",
            base_url="https://api.sarvam.ai/v1",
            transport=transport,
        )
        assert backend.chat("s", "u").text == "ok"
        call = calls[0]
        assert call["url"] == "https://api.sarvam.ai/v1/chat/completions"
        assert call["headers"]["Authorization"] == "Bearer sk"
        assert call["body"]["model"] == "sarvam-m"

    def test_http_error_raises_llm_error(self) -> None:
        def transport(
            method: str, url: str, headers: dict[str, str], body: bytes
        ) -> tuple[int, bytes]:
            raise LLMError("openai", "HTTP 429")

        backend = OpenAICompatBackend(
            "openai",
            api_key="k",
            model="gpt-4o-mini",
            base_url="https://api.openai.com/v1",
            transport=transport,
        )
        import pytest

        with pytest.raises(LLMError):
            backend.chat("s", "u")


class TestChainFallback:
    def test_first_backend_failure_falls_to_next(self) -> None:
        def failing(
            method: str, url: str, headers: dict[str, str], body: bytes
        ) -> tuple[int, bytes]:
            raise LLMError("gemini", "quota exceeded")

        gemini = GeminiBackend(api_key="k", transport=failing)
        transport, _ = _scripted([{"choices": [{"message": {"content": "from ollama"}}]}])
        ollama = OpenAICompatBackend(
            "ollama",
            api_key="ollama",
            model="llama3.1:8b",
            base_url="http://127.0.0.1:11434/v1",
            transport=transport,
        )
        chain = AIChain([gemini, ollama])
        response = chain.chat("s", "u")
        assert response.provider_id == "ollama"
        assert response.text == "from ollama"

    def test_all_fail_raises_chain_error(self) -> None:
        def failing(
            method: str, url: str, headers: dict[str, str], body: bytes
        ) -> tuple[int, bytes]:
            raise LLMError("x", "down")

        chain = AIChain([GeminiBackend(api_key="k", transport=failing)])
        import pytest

        with pytest.raises(AIChainError):
            chain.chat("s", "u")
