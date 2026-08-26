"""Sarvam AI REST client for Indic Speech-to-Text and Text-to-Speech (PRD §13.5.3).

Provides direct integration with Sarvam Saaras v3 (STT) and Bulbul v1/v2 (TTS).
Never throws unhandled exceptions — returns structured results with graceful fallbacks.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from app.config import get_settings

logger = logging.getLogger(__name__)

SARVAM_BASE_URL = "https://api.sarvam.ai"
SUPPORTED_LANGUAGES = (
    "en-IN",
    "hi-IN",
    "ta-IN",
    "te-IN",
    "kn-IN",
    "mr-IN",
    "bn-IN",
    "gu-IN",
    "ml-IN",
    "pa-IN",
    "unknown",
)


@dataclass(frozen=True)
class SarvamSTTResult:
    success: bool
    transcript: str
    language_code: str
    reason: str = "OK"


@dataclass(frozen=True)
class SarvamTTSResult:
    success: bool
    audio_base64: str | None
    content_type: str = "audio/wav"
    reason: str = "OK"


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


class SarvamClient:
    """Client for Sarvam AI speech-to-text (Saaras) and text-to-speech (Bulbul)."""

    def __init__(self, api_key: str | SecretStr | None = None, timeout_s: float = 15.0) -> None:
        settings = get_settings()
        raw_key = api_key or getattr(settings, "sarvam_api_key", None)
        if isinstance(raw_key, SecretStr):
            self.api_key: str | None = raw_key.get_secret_value()
        elif raw_key:
            self.api_key = str(raw_key).strip()
        else:
            self.api_key = os.environ.get("SARVAM_API_KEY") or _read_key_from_env_local(
                "SARVAM_API_KEY"
            )
        self.timeout_s = timeout_s

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 5)

    def transcribe(
        self,
        audio_bytes: bytes,
        language_code: str = "unknown",
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
    ) -> SarvamSTTResult:
        """Transcribe speech audio into text using Saaras v3."""
        if not self.is_configured:
            return SarvamSTTResult(
                success=False,
                transcript="",
                language_code=language_code,
                reason="SARVAM_API_KEY not configured",
            )

        headers = {
            "api-subscription-key": self.api_key or "",
        }

        # Language code normalization for Sarvam
        lang = language_code if language_code != "unknown" else "hi-IN"
        if lang not in SUPPORTED_LANGUAGES:
            lang = "hi-IN"

        files: dict[str, Any] = {
            "file": (filename, audio_bytes, content_type),
        }
        data: dict[str, Any] = {
            "model": "saaras:v3",
            "language_code": lang,
            "with_diarization": "false",
        }

        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                resp = client.post(
                    f"{SARVAM_BASE_URL}/speech-to-text",
                    headers=headers,
                    files=files,
                    data=data,
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    transcript = payload.get("transcript", "").strip()
                    detected_lang = payload.get("language_code", lang)
                    return SarvamSTTResult(
                        success=True,
                        transcript=transcript,
                        language_code=detected_lang,
                        reason="OK",
                    )
                return SarvamSTTResult(
                    success=False,
                    transcript="",
                    language_code=lang,
                    reason=f"Sarvam HTTP {resp.status_code}: {resp.text[:120]}",
                )
        except Exception as exc:
            logger.warning("Sarvam STT failed: %s", exc)
            return SarvamSTTResult(
                success=False,
                transcript="",
                language_code=lang,
                reason=f"Network/Request error: {exc}",
            )

    def synthesize(
        self,
        text: str,
        target_language_code: str = "en-IN",
        speaker: str | None = None,
    ) -> SarvamTTSResult:
        """Synthesize text into natural spoken speech using Bulbul."""
        if not self.is_configured:
            return SarvamTTSResult(
                success=False,
                audio_base64=None,
                reason="SARVAM_API_KEY not configured",
            )

        if not text.strip():
            return SarvamTTSResult(
                success=False,
                audio_base64=None,
                reason="Empty text for synthesis",
            )

        lang = (
            target_language_code
            if target_language_code
            in (
                "en-IN",
                "hi-IN",
                "ta-IN",
                "te-IN",
                "kn-IN",
                "mr-IN",
                "bn-IN",
                "gu-IN",
                "ml-IN",
                "pa-IN",
            )
            else "en-IN"
        )

        # Truncate to reasonable sentence length for instant sub-second synthesis
        clean_text = text.strip()[:500]

        settings = get_settings()
        resolved_speaker = speaker or settings.voice_tts_speaker

        headers = {
            "api-subscription-key": self.api_key or "",
            "Content-Type": "application/json",
        }
        payload = {
            "inputs": [clean_text],
            "target_language_code": lang,
            "speaker": resolved_speaker,
            "pace": settings.voice_tts_pace,
            "speech_sample_rate": settings.voice_tts_sample_rate,
            "enable_preprocessing": True,
            "model": settings.voice_tts_model,
        }

        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                resp = client.post(
                    f"{SARVAM_BASE_URL}/text-to-speech",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    audios = data.get("audios", [])
                    if audios and isinstance(audios[0], str):
                        return SarvamTTSResult(
                            success=True,
                            audio_base64=audios[0],
                            content_type="audio/wav",
                            reason="OK",
                        )
                return SarvamTTSResult(
                    success=False,
                    audio_base64=None,
                    reason=f"Sarvam TTS HTTP {resp.status_code}: {resp.text[:120]}",
                )
        except Exception as exc:
            logger.warning("Sarvam TTS failed: %s", exc)
            return SarvamTTSResult(
                success=False,
                audio_base64=None,
                reason=f"Network/Request error: {exc}",
            )
