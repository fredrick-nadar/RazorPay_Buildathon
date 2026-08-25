"""Server-side speech transcription and synthesis (PRD 13.5.3).

Optional provider-backed STT/TTS for the voice copilot. Providers are
strictly optional: when no API key is configured the endpoints raise
:class:`VoiceProviderUnavailable` and the API returns 501 with a
machine-readable fallback so the frontend uses on-device browser speech.

STT: multipart POST {base_url}/speech-to-text (Sarvam saarika models or any
compatible gateway). TTS: JSON POST {base_url}/text-to-speech (Sarvam bulbul
models). Audio and transcripts are never persisted; nothing enters the audit
log from this module.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import Settings

MAX_AUDIO_BYTES = 8 * 1024 * 1024  # 8 MB raw audio ceiling
MIN_AUDIO_BYTES = 256
MAX_TTS_CHARS = 500
_PROVIDER_TIMEOUT_S = 20.0


class VoiceProviderUnavailable(Exception):
    """Raised when no speech provider key is configured."""

    def __init__(self, reason: str = "no provider key configured") -> None:
        super().__init__(reason)
        self.reason = reason


class VoiceProviderError(Exception):
    """Raised when the configured provider fails or returns malformed data."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _decode_audio(audio_base64: str) -> bytes:
    """Accept a bare base64 payload or a data URL; enforce size caps."""
    payload = audio_base64.strip()
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    try:
        raw = base64.b64decode(payload, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise VoiceProviderError("audio payload is not valid base64") from exc
    if len(raw) < MIN_AUDIO_BYTES:
        raise VoiceProviderError("audio payload too small to transcribe")
    if len(raw) > MAX_AUDIO_BYTES:
        raise VoiceProviderError("audio payload exceeds the 8 MB ceiling")
    return raw


def _post_multipart(
    url: str,
    api_key: str,
    fields: dict[str, str],
    file_field: str,
    file_name: str,
    file_bytes: bytes,
    file_content_type: str,
) -> dict[str, Any]:
    boundary = f"----argus{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        part = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        parts.append(part.encode())
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
            f'filename="{file_name}"\r\nContent-Type: {file_content_type}\r\n\r\n'
        ).encode()
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "api-subscription-key": api_key,
        },
        method="POST",
    )
    return _send(request)


def _post_json(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "api-subscription-key": api_key,
        },
        method="POST",
    )
    return _send(request)


def _send(request: Request) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=_PROVIDER_TIMEOUT_S) as response:
            parsed: dict[str, Any] = json.loads(response.read().decode("utf-8"))
            return parsed
    except HTTPError as exc:
        raise VoiceProviderError(f"provider HTTP {exc.code}") from exc
    except URLError as exc:
        raise VoiceProviderError(f"provider unreachable: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise VoiceProviderError("provider returned malformed JSON") from exc


def transcribe_audio(
    audio_base64: str, language: str, content_type: str, settings: Settings
) -> dict[str, Any]:
    """Transcribe one utterance via the configured provider. Read-only."""
    key = settings.voice_stt_api_key
    if key is None or not key.get_secret_value():
        raise VoiceProviderUnavailable(
            "server speech-to-text is not configured; using on-device browser recognition"
        )
    audio = _decode_audio(audio_base64)
    payload = _post_multipart(
        f"{settings.voice_stt_base_url.rstrip('/')}/speech-to-text",
        key.get_secret_value(),
        {"model": settings.voice_stt_model, "language_code": language},
        "file",
        "utterance.webm",
        audio,
        content_type or "audio/webm",
    )
    transcript = str(payload.get("transcript", "")).strip()
    if not transcript:
        raise VoiceProviderError("provider returned an empty transcript")
    return {"transcript": transcript, "provider": f"sarvam:{settings.voice_stt_model}"}


def synthesize_speech(text: str, language: str, settings: Settings) -> dict[str, Any]:
    """Synthesize one briefing via the configured provider. Read-only."""
    key = settings.voice_tts_api_key
    if key is None or not key.get_secret_value():
        raise VoiceProviderUnavailable(
            "server text-to-speech is not configured; using browser speech synthesis"
        )
    clean = text.strip()[:MAX_TTS_CHARS]
    if not clean:
        raise VoiceProviderError("nothing to synthesize")
    payload = _post_json(
        f"{settings.voice_tts_base_url.rstrip('/')}/text-to-speech",
        key.get_secret_value(),
        {
            "text": clean,
            "target_language_code": language,
            "model": settings.voice_tts_model,
            "speaker": settings.voice_tts_speaker,
        },
    )
    audios = payload.get("audios")
    if not isinstance(audios, list) or not audios or not audios[0]:
        raise VoiceProviderError("provider returned no audio")
    return {"audio_base64": str(audios[0]), "content_type": "audio/wav"}


__all__ = [
    "MAX_AUDIO_BYTES",
    "VoiceProviderError",
    "VoiceProviderUnavailable",
    "synthesize_speech",
    "transcribe_audio",
]
