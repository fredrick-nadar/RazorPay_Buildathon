"""AI backend primitives: responses, errors, and the injectable transport.

Every backend (Groq, Gemini, OpenAI-compatible, Sarvam, Ollama) funnels through
``Transport`` - a callable that performs one HTTP POST and returns
(status, body_bytes). Tests inject scripted transports; production uses the
urllib default. No SDK dependency anywhere.

The transport takes an explicit per-call ``timeout_s`` so the value calculated
from the case deadline reaches ``urlopen`` for that specific attempt. Baking a
fixed timeout into the transport at construction time was the defect behind the
live acceptance failure: the outer case watchdog could fire long before the
transport gave up.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

# Fallback used only when no caller-supplied deadline is in play.
DEFAULT_ATTEMPT_TIMEOUT_S = 11.0

# Honest, fixed client identity.  ``urlopen`` otherwise sends
# ``Python-urllib/3.x``, which the edge in front of at least one provider host
# rejects with a bare 403 before the request reaches the API - measured as a
# 403 -> 200 flip on a single-variable experiment (cloud-reference section 38).
# It is deliberately a constant, not a setting: an operator-tunable identity
# would be one more way for a deployment to drift into being rejected.
USER_AGENT = "ARGUS-Control/1.0"


@dataclass(frozen=True)
class ToolCall:
    """One structured tool call returned by a provider's native protocol.

    ``arguments_json`` is kept as the provider returned it, unparsed: the
    investigator validates and parses it, and a malformed value must fail
    closed rather than be silently repaired here.
    """

    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class LLMResponse:
    """One successful completion from any backend.

    ``tool_calls`` is populated only by a backend running the native tool
    protocol; it is empty for every legacy text/JSON turn, so existing callers
    are unaffected. Provider reasoning is never captured: no field here can
    hold it, and no backend reads one.
    """

    text: str
    provider_id: str
    model: str
    latency_ms: float
    tool_calls: tuple[ToolCall, ...] = ()
    native_tool_protocol: bool = False


class LLMError(Exception):
    """A backend failed (network, HTTP status, malformed payload, timeout).

    The message deliberately excludes response bodies, prompts and credentials:
    a gateway can echo a submitted key, and this exception is surfaced into
    persisted diagnostics.
    """

    def __init__(
        self,
        provider_id: str,
        reason: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        timeout: bool = False,
    ) -> None:
        super().__init__(f"[{provider_id}] {reason}")
        self.provider_id = provider_id
        self.reason = reason
        self.status_code = status_code
        self.retryable = retryable
        self.timeout = timeout


class Transport(Protocol):
    """One HTTP POST bounded by ``timeout_s``, returning (status, body)."""

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout_s: float,
    ) -> tuple[int, bytes]: ...


def _with_user_agent(headers: dict[str, str]) -> dict[str, str]:
    """Add the ARGUS identity unless the caller already set one.

    HTTP header names are case-insensitive, so an explicit ``user-agent`` in
    any casing wins and the caller's dict is returned untouched.  Nothing else
    about the headers is altered.
    """
    if any(name.lower() == "user-agent" for name in headers):
        return headers
    return {**headers, "User-Agent": USER_AGENT}


def urllib_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout_s: float = DEFAULT_ATTEMPT_TIMEOUT_S,
) -> tuple[int, bytes]:
    """Default production transport; ``timeout_s`` is applied to this attempt."""
    request = urllib.request.Request(
        url, data=body, headers=_with_user_agent(headers), method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except TimeoutError as exc:
        raise LLMError("transport", "attempt timed out", retryable=True, timeout=True) from exc
    except urllib.error.URLError as exc:
        # A socket timeout surfaces as URLError(reason=timeout) on some stacks.
        if isinstance(exc.reason, TimeoutError):
            raise LLMError("transport", "attempt timed out", retryable=True, timeout=True) from exc
        raise LLMError("transport", "network connection failed", retryable=True) from exc


def urllib_transport_with_timeout(cap_s: float) -> Transport:
    """Production transport clamped to ``cap_s``.

    The per-call timeout still applies; the cap is an additional ceiling for
    callers that have no deadline of their own.
    """

    def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout_s: float = DEFAULT_ATTEMPT_TIMEOUT_S,
    ) -> tuple[int, bytes]:
        return urllib_transport(method, url, headers, body, min(cap_s, timeout_s))

    return transport


def post_json(
    transport: Transport,
    provider_id: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: float = DEFAULT_ATTEMPT_TIMEOUT_S,
) -> dict[str, Any]:
    """POST a JSON payload; raise LLMError on non-2xx or malformed body."""
    body = json.dumps(payload).encode("utf-8")
    headers = {**headers, "Content-Type": "application/json"}
    started = time.perf_counter()
    status, raw = transport("POST", url, headers, body, timeout_s)
    latency = (time.perf_counter() - started) * 1000.0
    if status < 200 or status >= 300:
        retryable = status in {408, 409, 425, 429} or status >= 500
        # The body is intentionally discarded: gateways echo request content,
        # including the Authorization header value in some error shapes.
        raise LLMError(
            provider_id,
            f"HTTP {status} after {latency:.0f}ms",
            status_code=status,
            retryable=retryable,
        )
    try:
        parsed: dict[str, Any] = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise LLMError(provider_id, "malformed JSON response") from exc
    return parsed


__all__ = [
    "DEFAULT_ATTEMPT_TIMEOUT_S",
    "LLMError",
    "LLMResponse",
    "ToolCall",
    "Transport",
    "USER_AGENT",
    "post_json",
    "urllib_transport",
    "urllib_transport_with_timeout",
]
