"""Natural-voice synthesis (PRD 13.5.3) - thin re-export of the TTS half.

Kept as a separate module so the API surface reads: transcribe.py owns the
listening path, speech.py owns the speaking path. Both are optional
providers; unconfigured -> VoiceProviderUnavailable -> 501 browser fallback.
"""

from __future__ import annotations

from app.voice.transcribe import (
    VoiceProviderError,
    VoiceProviderUnavailable,
    synthesize_speech,
)

__all__ = ["VoiceProviderError", "VoiceProviderUnavailable", "synthesize_speech"]
