"""AI backend primitives: responses, errors, and the injectable transport.

Every backend (Gemini, OpenAI-compatible, Sarvam, Ollama) funnels through
``Transport`` - a callable that performs one HTTP POST and returns
(status, body_bytes). Tests inject scripted transports; production uses the
urllib default. No SDK dependency anywhere.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMResponse:
    """One successful completion from any backend."""

    text: str
    provider_id: str
    model: str
    latency_ms: float


class LLMError(Exception):
    """A backend failed (network, HTTP status, malformed payload)."""

    def __init__(self, provider_id: str, reason: str) -> None:
        super().__init__(f"[{provider_id}] {reason}")
        self.provider_id = provider_id
        self.reason = reason


# (method, url, headers, body) -> (http_status, body_bytes)
Transport = Callable[[str, str, dict[str, str], bytes], tuple[int, bytes]]


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes
) -> tuple[int, bytes]:
    """Default production transport via urllib (no SDK dependencies)."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise LLMError("transport", f"network unreachable: {exc.reason}") from exc


def post_json(
    transport: Transport,
    provider_id: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST a JSON payload; raise LLMError on non-2xx or malformed body."""
    body = json.dumps(payload).encode("utf-8")
    headers = {**headers, "Content-Type": "application/json"}
    started = time.perf_counter()
    status, raw = transport("POST", url, headers, body)
    latency = (time.perf_counter() - started) * 1000.0
    if status < 200 or status >= 300:
        snippet = raw[:200].decode("utf-8", errors="replace")
        raise LLMError(provider_id, f"HTTP {status} after {latency:.0f}ms: {snippet}")
    try:
        parsed: dict[str, Any] = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise LLMError(provider_id, "malformed JSON response") from exc
    return parsed


__all__ = ["LLMError", "LLMResponse", "Transport", "post_json", "urllib_transport"]
