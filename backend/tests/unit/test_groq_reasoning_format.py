"""REVIEW-016: Groq requires ``reasoning_format`` in JSON mode.

Groq's GPT-OSS family defaults to ``reasoning_format: "raw"``, which Groq
rejects when ``response_format`` is used. The measured production failure was
``400 invalid_request_error / tool_use_failed`` (cloud-reference section 41).
ARGUS sends ``"hidden"``, never ``"parsed"``, because model reasoning must not
be exposed, parsed, logged or persisted.

Every test here is OFFLINE and uses a capturing transport. Nothing contacts
Groq or any other host.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from pydantic import SecretStr

from app.ai.chain import build_chain
from app.ai.openai_compat import (
    GROQ_JSON_REASONING_FORMAT,
    GROQ_PROVIDER_ID,
    OpenAICompatBackend,
)
from app.ai.policy import PROVIDER_REQUEST_PROTOCOL_VERSION, policy_from_settings
from app.config import Settings

SENTINEL_KEY = "gsk_" + "R" * 40
ROTATED_KEY = "gsk_" + "Q" * 40

# The payload ARGUS sent before this correction, captured from the real backend
# and recorded here as the contract that must now FAIL.
PRE_CORRECTION_GROQ_PAYLOAD = {
    "model": "openai/gpt-oss-20b",
    "messages": [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ],
    "temperature": 0.1,
    "response_format": {"type": "json_object"},
}


class Capture:
    """Records exactly what the backend handed to the transport."""

    def __init__(self) -> None:
        self.calls = 0
        self.method: str | None = None
        self.url: str | None = None
        self.headers: dict[str, str] = {}
        self.body: bytes = b""
        self.timeout: float | None = None

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout_s: float,
    ) -> tuple[int, bytes]:
        self.calls += 1
        self.method = method
        self.url = url
        self.headers = dict(headers)
        self.body = body
        self.timeout = timeout_s
        return 200, json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.body)


def satisfies_groq_json_contract(payload: dict[str, Any]) -> bool:
    """The contract this correction introduces, as a single predicate."""
    return (
        payload.get("response_format") == {"type": "json_object"}
        and payload.get("reasoning_format") == GROQ_JSON_REASONING_FORMAT
    )


def _backend(provider_id: str, transport: Capture) -> OpenAICompatBackend:
    return OpenAICompatBackend(
        provider_id=provider_id,
        api_key=SENTINEL_KEY,
        model="openai/gpt-oss-20b",
        base_url="https://host.test/v1",
        transport=transport,
    )


# ---------------------------------------------------------------------------
# The correction itself
# ---------------------------------------------------------------------------


def test_groq_json_mode_sends_reasoning_format_hidden() -> None:
    capture = Capture()
    _backend(GROQ_PROVIDER_ID, capture).chat("SYS", "USER", json_mode=True, timeout_s=5.0)

    payload = capture.payload
    assert payload["reasoning_format"] == "hidden"
    assert GROQ_JSON_REASONING_FORMAT == "hidden"
    # response_format is preserved exactly.
    assert payload["response_format"] == {"type": "json_object"}
    # Exactly one field was added, nothing else.
    assert set(payload) == {
        "model",
        "messages",
        "temperature",
        "response_format",
        "reasoning_format",
    }


def test_argus_never_asks_groq_to_return_reasoning() -> None:
    """ "parsed" would surface reasoning; ARGUS must only ever send "hidden"."""
    capture = Capture()
    _backend(GROQ_PROVIDER_ID, capture).chat("SYS", "USER", json_mode=True, timeout_s=5.0)
    assert capture.payload["reasoning_format"] != "parsed"
    assert capture.payload["reasoning_format"] != "raw"


def test_groq_text_mode_omits_reasoning_format() -> None:
    capture = Capture()
    _backend(GROQ_PROVIDER_ID, capture).chat("SYS", "USER", json_mode=False, timeout_s=5.0)

    payload = capture.payload
    assert "reasoning_format" not in payload
    assert "response_format" not in payload
    assert set(payload) == {"model", "messages", "temperature"}


@pytest.mark.parametrize("provider_id", ["openai", "sarvam", "ollama", "anything-else"])
@pytest.mark.parametrize("json_mode", [True, False])
def test_non_groq_providers_are_byte_for_byte_unchanged(provider_id: str, json_mode: bool) -> None:
    capture = Capture()
    _backend(provider_id, capture).chat("SYS", "USER", json_mode=json_mode, timeout_s=5.0)

    payload = capture.payload
    assert "reasoning_format" not in payload
    expected = {"model", "messages", "temperature"}
    if json_mode:
        expected |= {"response_format"}
        assert payload["response_format"] == {"type": "json_object"}
    assert set(payload) == expected
    assert payload["temperature"] == 0.1
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]


# ---------------------------------------------------------------------------
# The old payload must now fail, the new one must pass
# ---------------------------------------------------------------------------


def test_pre_correction_payload_fails_the_new_contract() -> None:
    assert satisfies_groq_json_contract(PRE_CORRECTION_GROQ_PAYLOAD) is False
    assert "reasoning_format" not in PRE_CORRECTION_GROQ_PAYLOAD


def test_corrected_payload_passes_the_new_contract() -> None:
    capture = Capture()
    _backend(GROQ_PROVIDER_ID, capture).chat("SYS", "USER", json_mode=True, timeout_s=5.0)
    assert satisfies_groq_json_contract(capture.payload) is True
    # Everything the old payload carried is still carried, unchanged.
    for key, value in PRE_CORRECTION_GROQ_PAYLOAD.items():
        assert capture.payload[key] == value


# ---------------------------------------------------------------------------
# Nothing else about the request may change
# ---------------------------------------------------------------------------


def test_headers_url_serialization_and_timeout_are_unchanged() -> None:
    capture = Capture()
    _backend(GROQ_PROVIDER_ID, capture).chat("SYS", "USER", json_mode=True, timeout_s=6.5)

    assert capture.method == "POST"
    assert capture.url == "https://host.test/v1/chat/completions"
    assert capture.headers["Authorization"] == f"Bearer {SENTINEL_KEY}"
    assert capture.timeout == 6.5
    # Serialization is still compact JSON bytes produced by post_json.
    assert capture.body == json.dumps(capture.payload).encode("utf-8")
    assert capture.calls == 1


def test_sarvam_keeps_its_extra_header_and_gains_nothing() -> None:
    capture = Capture()
    _backend("sarvam", capture).chat("SYS", "USER", json_mode=True, timeout_s=5.0)
    assert capture.headers["api-subscription-key"] == SENTINEL_KEY
    assert "reasoning_format" not in capture.payload


def test_caller_provided_transport_behaviour_is_unchanged() -> None:
    """An injected transport still receives the same contract and result."""
    capture = Capture()
    backend = _backend(GROQ_PROVIDER_ID, capture)
    response = backend.chat("SYS", "USER", json_mode=True, timeout_s=5.0)

    assert capture.calls == 1
    assert set(capture.headers) == {"Authorization", "Content-Type"}
    assert response.provider_id == GROQ_PROVIDER_ID
    assert response.model == "openai/gpt-oss-20b"
    assert response.text == "{}"


def test_no_reasoning_content_is_parsed_logged_or_persisted() -> None:
    """A response carrying reasoning must not surface it anywhere."""

    def reasoning_transport(
        _method: str,
        _url: str,
        _headers: dict[str, str],
        _body: bytes,
        _timeout_s: float,
    ) -> tuple[int, bytes]:
        return 200, json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": "{}",
                            "reasoning": "SECRET_CHAIN_OF_THOUGHT",
                        }
                    }
                ]
            }
        ).encode()

    backend = OpenAICompatBackend(
        provider_id=GROQ_PROVIDER_ID,
        api_key=SENTINEL_KEY,
        model="openai/gpt-oss-20b",
        base_url="https://host.test/v1",
        transport=reasoning_transport,
    )
    response = backend.chat("SYS", "USER", json_mode=True, timeout_s=5.0)

    assert response.text == "{}"
    assert "SECRET_CHAIN_OF_THOUGHT" not in response.text
    # LLMResponse has no field that could carry reasoning at all.
    assert not hasattr(response, "reasoning")
    assert "SECRET_CHAIN_OF_THOUGHT" not in repr(response)


# ---------------------------------------------------------------------------
# Execution-policy identity
# ---------------------------------------------------------------------------


def _groq_settings(key: str = SENTINEL_KEY) -> Settings:
    return Settings().model_copy(update={"ai_provider": "groq", "groq_api_key": SecretStr(key)})


def test_provider_request_protocol_version_is_in_the_policy_and_fingerprint() -> None:
    policy = policy_from_settings(_groq_settings())
    assert policy.provider_request_protocol_version == PROVIDER_REQUEST_PROTOCOL_VERSION
    assert "provider_request_protocol_version" in policy.describe()


def test_changing_the_provider_request_version_changes_the_fingerprint() -> None:
    policy = policy_from_settings(_groq_settings())
    older = replace(policy, provider_request_protocol_version="provider-request-v1")
    assert policy.fingerprint() != older.fingerprint()


def test_the_provider_request_version_is_distinct_from_the_other_versions() -> None:
    """It must not be disguised as a prompt, tool or result-schema change."""
    policy = policy_from_settings(_groq_settings())
    described = policy.describe()
    assert described["provider_request_protocol_version"] != described["prompt_protocol_version"]
    assert described["provider_request_protocol_version"] != described["tool_protocol_version"]
    assert described["provider_request_protocol_version"] != described["result_schema_version"]
    # Bumping the wire version alone must not alter the others.
    bumped = replace(policy, provider_request_protocol_version="provider-request-v99")
    assert bumped.prompt_protocol_version == policy.prompt_protocol_version
    assert bumped.tool_protocol_version == policy.tool_protocol_version
    assert bumped.result_schema_version == policy.result_schema_version


def test_key_rotation_still_does_not_change_the_fingerprint() -> None:
    original = policy_from_settings(_groq_settings(SENTINEL_KEY))
    rotated = policy_from_settings(_groq_settings(ROTATED_KEY))
    assert original.fingerprint() == rotated.fingerprint()


def test_no_key_material_appears_in_the_policy_identity() -> None:
    policy = policy_from_settings(_groq_settings())
    material = json.dumps(policy.describe(), sort_keys=True)
    assert SENTINEL_KEY not in material
    assert "gsk_" not in material
    assert SENTINEL_KEY not in policy.fingerprint()


def test_the_chain_built_from_settings_sends_the_corrected_payload() -> None:
    """End to end through build_chain, not just the backend in isolation."""
    capture = Capture()
    settings = _groq_settings()
    chain = build_chain(settings, transport=capture, policy=policy_from_settings(settings))
    assert chain.member_ids == ["groq"]
    chain.members[0].chat("SYS", "USER", json_mode=True, timeout_s=5.0)
    assert satisfies_groq_json_contract(capture.payload) is True
