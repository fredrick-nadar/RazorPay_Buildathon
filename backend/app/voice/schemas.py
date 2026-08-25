"""Voice Control Layer API schemas (PRD 13.5).

All models are strict Pydantic v2. Transcripts are capped at 280 characters
(sensitive-data minimization for the audit trail); no audio ever reaches the
backend - only text transcripts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.voice.enums import (
    ForbiddenVoiceIntent,
    VoiceIntent,
    VoiceLanguage,
    VoiceRequestStatus,
)

MAX_TRANSCRIPT_CHARS = 280


class VoiceParseRequest(BaseModel):
    """Raw user utterance (typed fallback or speech transcript)."""

    transcript: str = Field(min_length=1, max_length=MAX_TRANSCRIPT_CHARS)
    language: VoiceLanguage = VoiceLanguage.EN_IN

    @field_validator("transcript")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("transcript must not be empty")
        return stripped


class VoiceEntity(BaseModel):
    """Typed entities extracted from the utterance."""

    case_id: str | None = None
    spoken_case_ref: str | None = None
    amount_paise: int | None = None
    status: str | None = None
    category: str | None = None


class VoiceParseResult(BaseModel):
    """Server-side canonical interpretation. The token is the ONLY way to
    execute: it binds the server's own parse (never client-echoed intent)."""

    token: str
    transcript: str
    language: VoiceLanguage
    status: VoiceRequestStatus
    intent: VoiceIntent | None = None
    forbidden_intent: ForbiddenVoiceIntent | None = None
    entities: VoiceEntity = Field(default_factory=VoiceEntity)
    requires_confirmation: bool = False
    message: str
    message_key: str
    audio_base64: str | None = None
    content_type: str | None = None


class VoiceExecuteRequest(BaseModel):
    """Execute a previously parsed intent. `confirmed` is required for
    state-changing-but-safe intents (run reconciliation, prepare previews)."""

    token: str = Field(min_length=8, max_length=64)
    confirmed: bool = False


class VoiceCaseCard(BaseModel):
    """Compact case summary for voice briefings and lists."""

    case_id: str
    category: str
    status: str
    variance_paise: int
    currency: str
    summary: str


class VoicePreviewCard(BaseModel):
    """An existing DRAFT dry-run correction surfaced by voice."""

    case_id: str
    correction_id: str
    proposed_delta_paise: int
    variance_before_paise: int
    variance_after_paise: int
    status: str


class VoiceExecutionResult(BaseModel):
    """Outcome of executing one canonical voice intent."""

    status: VoiceRequestStatus
    intent: VoiceIntent | None = None
    message: str
    message_key: str
    language: VoiceLanguage
    cases: list[VoiceCaseCard] = Field(default_factory=list)
    previews: list[VoicePreviewCard] = Field(default_factory=list)
    briefing: str | None = None
    navigation: dict[str, Any] | None = None
    run: dict[str, Any] | None = None
    audit_event_id: str | None = None
    audio_base64: str | None = None
    content_type: str | None = None


class VoiceLanguageInfo(BaseModel):
    """Honest per-language capability label (PRD 13.5.3)."""

    code: VoiceLanguage
    label: str
    tier: Literal[1]
    status: Literal["ARGUS_TESTED", "AVAILABLE_FROM_PROVIDER"]


class VoiceLanguagesResponse(BaseModel):
    languages: list[VoiceLanguageInfo]
    policy: str


class VoiceRefusalInfo(BaseModel):
    forbidden_intent: ForbiddenVoiceIntent
    message: str


class VoiceSynthesizeRequest(BaseModel):
    """Alias request for /voice/synthesize; provider/speaker are accepted for
    client compatibility but the configured gateway decides the engine."""

    text: str = Field(min_length=1, max_length=1000)
    language: VoiceLanguage = VoiceLanguage.EN_IN
    provider: str = "auto"
    speaker: str = "meera"


class VoiceSynthesizeResponse(BaseModel):
    success: bool
    audio_base64: str | None = None
    content_type: str = "audio/wav"
    provider: str = "none"
    reason: str = "OK"


class VoiceCommandRequest(BaseModel):
    """One-round-trip command: parse, guard, and (when safe) execute."""

    transcript: str = Field(min_length=1, max_length=MAX_TRANSCRIPT_CHARS)
    language: VoiceLanguage = VoiceLanguage.EN_IN
    confirmed: bool = False

    @field_validator("transcript")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("transcript must not be empty")
        return stripped


class VoiceTranscribeRequest(BaseModel):
    """Recorded-audio transcription request (base64 or data URL)."""

    audio_base64: str = Field(min_length=100, max_length=12_000_000)
    language: VoiceLanguage = VoiceLanguage.EN_IN
    content_type: str = "audio/webm"


class VoiceTTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    language: VoiceLanguage = VoiceLanguage.EN_IN


__all__ = [
    "MAX_TRANSCRIPT_CHARS",
    "VoiceCaseCard",
    "VoiceCommandRequest",
    "VoiceEntity",
    "VoiceExecuteRequest",
    "VoiceExecutionResult",
    "VoiceLanguagesResponse",
    "VoiceParseRequest",
    "VoiceParseResult",
    "VoicePreviewCard",
    "VoiceRefusalInfo",
    "VoiceSynthesizeRequest",
    "VoiceSynthesizeResponse",
    "VoiceTTSRequest",
    "VoiceTranscribeRequest",
]
