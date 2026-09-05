"""Transport identity regressions for the shared urllib transport.

`urlopen` sends ``Python-urllib/3.x`` when no ``User-Agent`` is supplied, and
the edge in front of at least one provider host rejects that with a bare 403
before the request reaches the API (cloud-reference sections 37-38). The
transport now sends a fixed honest identity instead.

Every test here is OFFLINE: ``urllib.request.urlopen`` is replaced with a
capture function, so the constructed ``Request`` is inspected and no socket is
ever opened. Because this is the SHARED transport, the suite also proves the
existing Groq, Sarvam, Gemini, Ollama and schema-mapping callers keep their
headers, payloads and timeouts exactly as before.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from app.ai.base import (
    DEFAULT_ATTEMPT_TIMEOUT_S,
    USER_AGENT,
    LLMError,
    post_json,
    urllib_transport,
    urllib_transport_with_timeout,
)
from app.ai.chain import build_chain
from app.ai.policy import policy_from_settings
from app.config import Settings

SENTINEL_KEY = "gsk_" + "T" * 40


class _Captured:
    """One captured urlopen call: the Request object and the timeout."""

    def __init__(self) -> None:
        self.request: Any = None
        self.timeout: float | None = None
        self.calls = 0


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> _Captured:
    """Replace urlopen so the constructed Request is inspectable, offline."""
    captured = _Captured()

    class _Response:
        status = 200

        def read(self) -> bytes:
            # Satisfies both the OpenAI-compatible and the Gemini response
            # shapes, so `chat()` completes and the captured Request - the
            # thing under test - is not masked by a parse error.
            return json.dumps(
                {
                    "choices": [{"message": {"content": "{}"}}],
                    "candidates": [{"content": {"parts": [{"text": "{}"}]}}],
                }
            ).encode()

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    def fake_urlopen(request: Any, timeout: float | None = None) -> _Response:
        captured.calls += 1
        captured.request = request
        captured.timeout = timeout
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def _header(request: Any, name: str) -> str | None:
    """urllib normalizes header names; look them up case-insensitively."""
    for key, value in request.header_items():
        if key.lower() == name.lower():
            return str(value)
    return None


# ---------------------------------------------------------------------------
# The identity itself
# ---------------------------------------------------------------------------


def test_default_user_agent_is_the_argus_identity(capture: _Captured) -> None:
    urllib_transport("POST", "https://example.test/x", {}, b"{}", 5.0)
    assert _header(capture.request, "User-Agent") == USER_AGENT
    assert USER_AGENT == "ARGUS-Control/1.0"


def test_python_urllib_identity_is_absent(capture: _Captured) -> None:
    urllib_transport("POST", "https://example.test/x", {"Content-Type": "a/b"}, b"{}", 5.0)
    agent = _header(capture.request, "User-Agent")
    assert agent is not None
    assert "Python-urllib" not in agent
    # And nothing else in the request carries the interpreter default.
    assert all("Python-urllib" not in str(value) for _k, value in capture.request.header_items())


def test_explicit_caller_user_agent_wins(capture: _Captured) -> None:
    urllib_transport(
        "POST",
        "https://example.test/x",
        {"User-Agent": "Caller/9.9"},
        b"{}",
        5.0,
    )
    assert _header(capture.request, "User-Agent") == "Caller/9.9"


@pytest.mark.parametrize("name", ["user-agent", "USER-AGENT", "User-Agent", "uSeR-aGeNt"])
def test_explicit_user_agent_wins_in_any_casing(capture: _Captured, name: str) -> None:
    urllib_transport("POST", "https://example.test/x", {name: "Caller/1.0"}, b"{}", 5.0)
    assert _header(capture.request, "User-Agent") == "Caller/1.0"


def test_caller_header_mapping_is_not_mutated(capture: _Captured) -> None:
    headers = {"Authorization": f"Bearer {SENTINEL_KEY}"}
    urllib_transport("POST", "https://example.test/x", headers, b"{}", 5.0)
    assert headers == {"Authorization": f"Bearer {SENTINEL_KEY}"}, "caller dict was mutated"


# ---------------------------------------------------------------------------
# Everything else about the request must be unchanged
# ---------------------------------------------------------------------------


def test_authorization_body_method_and_url_are_unchanged(capture: _Captured) -> None:
    headers = {"Authorization": f"Bearer {SENTINEL_KEY}", "Content-Type": "application/json"}
    body = b'{"model": "m"}'
    urllib_transport("POST", "https://example.test/v1/chat", headers, body, 7.0)

    request = capture.request
    assert request.get_method() == "POST"
    assert request.full_url == "https://example.test/v1/chat"
    assert request.data == body
    assert _header(request, "Authorization") == f"Bearer {SENTINEL_KEY}"
    assert _header(request, "Content-Type") == "application/json"


def test_timeout_reaches_urlopen_unchanged(capture: _Captured) -> None:
    urllib_transport("POST", "https://example.test/x", {}, b"{}", 3.25)
    assert capture.timeout == 3.25


def test_capped_transport_still_forwards_the_smaller_timeout(capture: _Captured) -> None:
    transport = urllib_transport_with_timeout(2.0)
    transport("POST", "https://example.test/x", {}, b"{}", 9.0)
    assert capture.timeout == 2.0
    assert _header(capture.request, "User-Agent") == USER_AGENT


def test_default_timeout_constant_is_unchanged(capture: _Captured) -> None:
    urllib_transport("POST", "https://example.test/x", {}, b"{}")
    assert capture.timeout == DEFAULT_ATTEMPT_TIMEOUT_S


# ---------------------------------------------------------------------------
# Error behaviour must be preserved, and must never carry the key
# ---------------------------------------------------------------------------


def test_http_error_still_returns_status_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising(_request: Any, timeout: float | None = None) -> Any:
        raise urllib.error.HTTPError(
            "https://example.test/x", 403, "Forbidden", {}, io_body(b"denied")
        )

    monkeypatch.setattr(urllib.request, "urlopen", raising)
    status, body = urllib_transport("POST", "https://example.test/x", {}, b"{}", 1.0)
    assert status == 403
    assert body == b"denied"


def io_body(payload: bytes) -> Any:
    import io

    return io.BytesIO(payload)


def test_timeout_error_is_still_classified_as_retryable_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raising(_request: Any, timeout: float | None = None) -> Any:
        raise TimeoutError("slow")

    monkeypatch.setattr(urllib.request, "urlopen", raising)
    with pytest.raises(LLMError) as caught:
        urllib_transport("POST", "https://example.test/x", {}, b"{}", 1.0)
    assert caught.value.timeout is True
    assert caught.value.retryable is True


def test_no_secret_reaches_a_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising(_request: Any, timeout: float | None = None) -> Any:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", raising)
    with pytest.raises(LLMError) as caught:
        urllib_transport(
            "POST",
            "https://example.test/x",
            {"Authorization": f"Bearer {SENTINEL_KEY}"},
            b"{}",
            1.0,
        )
    leaked = SENTINEL_KEY in str(caught.value) or SENTINEL_KEY in caught.value.reason
    assert not leaked


def test_no_secret_reaches_a_non_2xx_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raising(_request: Any, timeout: float | None = None) -> Any:
        raise urllib.error.HTTPError(
            "https://example.test/x", 403, "Forbidden", {}, io_body(b"denied")
        )

    monkeypatch.setattr(urllib.request, "urlopen", raising)
    with pytest.raises(LLMError) as caught:
        post_json(
            urllib_transport,
            "groq",
            "https://example.test/x",
            {"Authorization": f"Bearer {SENTINEL_KEY}"},
            {"model": "m"},
            1.0,
        )
    leaked = SENTINEL_KEY in str(caught.value)
    assert not leaked
    assert caught.value.status_code == 403


# ---------------------------------------------------------------------------
# Shared transport: every existing caller keeps its headers and payload
# ---------------------------------------------------------------------------


def _settings_with_all_providers() -> Settings:
    from pydantic import SecretStr

    return Settings().model_copy(
        update={
            "ai_provider": "auto",
            "groq_api_key": SecretStr(SENTINEL_KEY),
            "gemini_api_key": SecretStr(SENTINEL_KEY),
            "openai_api_key": SecretStr(SENTINEL_KEY),
            "sarvam_api_key": SecretStr(SENTINEL_KEY),
            "ollama_enabled": True,
        }
    )


@pytest.mark.parametrize(
    ("provider_id", "expected_auth_headers", "url_fragment"),
    [
        ("groq", {"authorization"}, "/chat/completions"),
        ("openai", {"authorization"}, "/chat/completions"),
        ("sarvam", {"authorization", "api-subscription-key"}, "/chat/completions"),
        ("ollama", {"authorization"}, "/chat/completions"),
        ("gemini", {"x-goog-api-key"}, ":generateContent"),
    ],
)
def test_existing_providers_keep_their_headers_and_payload(
    capture: _Captured,
    provider_id: str,
    expected_auth_headers: set[str],
    url_fragment: str,
) -> None:
    settings = _settings_with_all_providers()
    chain = build_chain(settings, policy=policy_from_settings(settings))
    member = next(m for m in chain.members if m.provider_id == provider_id)

    member.chat("SYS", "USER", json_mode=True, timeout_s=6.5)

    request = capture.request
    names = {key.lower() for key, _value in request.header_items()}
    # The provider's own auth headers survive untouched.
    assert expected_auth_headers <= names
    assert "content-type" in names
    # The new identity is present and is the ARGUS one.
    assert _header(request, "User-Agent") == USER_AGENT
    # Nothing beyond the provider's headers plus content-type and the identity.
    assert names == expected_auth_headers | {"content-type", "user-agent"}
    assert url_fragment in request.full_url
    assert capture.timeout == 6.5
    # The payload is still the provider's own shape.
    payload = json.loads(request.data)
    if provider_id == "gemini":
        assert set(payload) == {"systemInstruction", "contents", "generationConfig"}
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
    else:
        expected_keys = {"model", "messages", "temperature", "response_format"}
        if provider_id == "groq":
            # Groq requires reasoning_format in JSON mode (REVIEW-016).
            expected_keys |= {"reasoning_format"}
        assert set(payload) == expected_keys
        assert payload["response_format"] == {"type": "json_object"}
        assert [m["role"] for m in payload["messages"]] == ["system", "user"]


def test_schema_mapping_caller_keeps_its_authorization(capture: _Captured) -> None:
    """The non-AI caller of the shared transport is unaffected too."""
    post_json(
        urllib_transport,
        "schema-mapping",
        "https://example.test/v1/chat/completions",
        {"Authorization": f"Bearer {SENTINEL_KEY}"},
        {"model": "m", "messages": []},
        4.0,
    )
    request = capture.request
    assert _header(request, "Authorization") == f"Bearer {SENTINEL_KEY}"
    assert _header(request, "Content-Type") == "application/json"
    assert _header(request, "User-Agent") == USER_AGENT
    assert capture.timeout == 4.0


def test_scripted_transports_are_untouched_by_the_identity() -> None:
    """Injected transports still receive exactly what the backend passes."""
    seen: dict[str, Any] = {}

    def scripted(
        method: str, url: str, headers: dict[str, str], body: bytes, timeout_s: float
    ) -> tuple[int, bytes]:
        seen.update(method=method, url=url, headers=dict(headers), timeout=timeout_s)
        return 200, json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()

    settings = _settings_with_all_providers().model_copy(update={"ai_provider": "groq"})
    chain = build_chain(settings, transport=scripted, policy=policy_from_settings(settings))
    chain.members[0].chat("SYS", "USER", json_mode=True, timeout_s=2.0)

    # The identity is added by the urllib transport, not by the backends, so a
    # scripted transport sees the original header set and its own contract.
    assert set(seen["headers"]) == {"Authorization", "Content-Type"}
    assert seen["timeout"] == 2.0
