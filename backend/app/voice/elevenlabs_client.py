"""ElevenLabs REST client for low-latency human-like speech synthesis.

Supports Flash v2.5 / Turbo v2.5 for sub-250ms voice generation.
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import SecretStr

from app.config import get_settings

logger = logging.getLogger(__name__)

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel / executive voice


def _read_key_from_env_local(key_name: str) -> str | None:
    env_local = Path(".env.local")
    if env_local.is_file():
        try:
            for line in env_local.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() == key_name and v.strip():
                        return v.strip().strip("\"'")
        except Exception:
            pass
    return None


@dataclass(frozen=True)
class ElevenLabsTTSResult:
    success: bool
    audio_base64: str | None
    content_type: str = "audio/mpeg"
    reason: str = "OK"


class ElevenLabsClient:
    """Client for ElevenLabs low-latency voice synthesis."""

    def __init__(
        self,
        api_key: str | SecretStr | None = None,
        voice_id: str | None = None,
        timeout_s: float = 12.0,
    ) -> None:
        settings = get_settings()
        raw_key = api_key or getattr(settings, "elevenlabs_api_key", None)
        if isinstance(raw_key, SecretStr):
            self.api_key: str | None = raw_key.get_secret_value()
        elif raw_key:
            self.api_key = str(raw_key).strip()
        else:
            self.api_key = os.environ.get("ELEVENLABS_API_KEY") or _read_key_from_env_local(
                "ELEVENLABS_API_KEY"
            )

        self.voice_id = (
            voice_id or getattr(settings, "elevenlabs_voice_id", None) or DEFAULT_VOICE_ID
        )
        self.timeout_s = timeout_s

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 5)

    def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        model_id: str = "eleven_flash_v2_5",
    ) -> ElevenLabsTTSResult:
        """Synthesize text into high-fidelity MP3 audio."""
        if not self.is_configured:
            return ElevenLabsTTSResult(
                success=False,
                audio_base64=None,
                reason="ELEVENLABS_API_KEY not configured",
            )

        if not text.strip():
            return ElevenLabsTTSResult(
                success=False,
                audio_base64=None,
                reason="Empty text for synthesis",
            )

        target_voice = voice_id or self.voice_id
        headers = {
            "xi-api-key": self.api_key or "",
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text.strip()[:500],
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        }

        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                resp = client.post(
                    f"{ELEVENLABS_BASE_URL}/text-to-speech/{target_voice}",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code == 200:
                    audio_b64 = base64.b64encode(resp.content).decode("ascii")
                    return ElevenLabsTTSResult(
                        success=True,
                        audio_base64=audio_b64,
                        content_type="audio/mpeg",
                        reason="OK",
                    )
                return ElevenLabsTTSResult(
                    success=False,
                    audio_base64=None,
                    reason=f"ElevenLabs HTTP {resp.status_code}: {resp.text[:120]}",
                )
        except Exception as exc:
            logger.warning("ElevenLabs TTS failed: %s", exc)
            return ElevenLabsTTSResult(
                success=False,
                audio_base64=None,
                reason=f"Network/Request error: {exc}",
            )
