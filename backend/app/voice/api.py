"""Voice Control API routes (PRD 13.5).

Endpoints:
- POST /api/v1/voice/parse        - classify one utterance; returns an opaque
  execution token for allowed intents; refuses and audits forbidden commands.
- POST /api/v1/voice/execute      - execute a server-parsed token; confirmation
  required for run reconciliation and preview preparation.
- POST /api/v1/voice/command      - atomic parse -> guard -> execute in one
  round trip (latency path); confirmation-aware.
- POST /api/v1/voice/transcribe   - OPTIONAL server-side STT (Sarvam-compatible).
  501 + machine-readable fallback when no key is configured, so the client
  uses on-device browser recognition.
- POST /api/v1/voice/tts          - OPTIONAL server-side natural-voice
  synthesis. 501 + fallback to browser speech synthesis when unconfigured.
- POST /api/v1/voice/synthesize   - alias of /tts with provider/speaker hints.
- GET  /api/v1/voice/languages    - honest per-language capability labels.
- GET  /api/v1/voice/capabilities - honest STT/TTS engine availability.

Audio and synthesized speech are never persisted; nothing in this module
writes financial state.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.persistence.database import Database
from app.voice import service
from app.voice.schemas import (
    VoiceCommandRequest,
    VoiceExecuteRequest,
    VoiceLanguagesResponse,
    VoiceParseRequest,
    VoiceParseResult,
    VoiceTranscribeRequest,
    VoiceTTSRequest,
)
from app.voice.speech import VoiceProviderError, VoiceProviderUnavailable, synthesize_speech
from app.voice.transcribe import transcribe_audio

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])


@router.post("/parse")
def parse_voice_command(payload: VoiceParseRequest, request: Request) -> VoiceParseResult:
    db: Database = request.app.state.db
    return service.parse_command(payload.transcript, payload.language, db=db)


@router.post("/execute")
def execute_voice_command(payload: VoiceExecuteRequest, request: Request) -> dict[str, object]:
    db: Database = request.app.state.db
    result = service.execute_command(db, payload.token, confirmed=payload.confirmed)
    return result.model_dump()


@router.post("/command")
def voice_command(payload: VoiceCommandRequest, request: Request) -> dict[str, object]:
    """Atomic parse -> guard -> execute. One round trip for the fast path."""
    db: Database = request.app.state.db
    return service.command(db, payload.transcript, payload.language, confirmed=payload.confirmed)


@router.post("/transcribe", response_model=None)
def transcribe_voice_audio(payload: VoiceTranscribeRequest) -> dict[str, object] | JSONResponse:
    """Optional server-side STT. 501 + machine-readable fallback when the
    provider key is unset, so the copilot uses on-device recognition."""
    settings = get_settings()
    try:
        result = transcribe_audio(
            payload.audio_base64, payload.language.value, payload.content_type, settings
        )
    except VoiceProviderUnavailable as exc:
        return JSONResponse(
            status_code=501,
            content={"status": "unavailable", "fallback": "browser", "reason": exc.reason},
        )
    except VoiceProviderError as exc:
        return JSONResponse(
            status_code=502,
            content={"status": "provider_error", "fallback": "browser", "reason": exc.reason},
        )
    return {"success": True, **result}


@router.post("/tts", response_model=None)
def synthesize_voice(payload: VoiceTTSRequest) -> dict[str, object] | JSONResponse:
    """Optional server-side natural-voice synthesis. 501 fallback otherwise."""
    settings = get_settings()
    try:
        result = synthesize_speech(payload.text, payload.language.value, settings)
    except VoiceProviderUnavailable as exc:
        return JSONResponse(
            status_code=501,
            content={"status": "unavailable", "fallback": "browser", "reason": exc.reason},
        )
    except VoiceProviderError as exc:
        return JSONResponse(
            status_code=502,
            content={"status": "provider_error", "fallback": "browser", "reason": exc.reason},
        )
    return {"success": True, **result}


@router.post("/synthesize", response_model=None)
def synthesize_voice_alias(payload: VoiceTTSRequest) -> dict[str, object] | JSONResponse:
    """Alias of /tts for clients that use the /synthesize verb."""
    return synthesize_voice(payload)


@router.get("/languages")
def list_voice_languages() -> VoiceLanguagesResponse:
    return VoiceLanguagesResponse(
        languages=service.languages(),
        policy=(
            "Voice can read, brief, navigate, and (with confirmation) trigger a batch "
            "run or list existing previews. Voice can never approve, apply, edit, "
            "override, or move money - refusals are audited."
        ),
    )


@router.get("/capabilities")
def voice_capabilities() -> dict[str, str]:
    """Honest engine availability so the client picks the right path."""
    settings = get_settings()
    stt = "sarvam" if settings.voice_stt_api_key is not None else "unavailable"
    tts = "sarvam" if settings.voice_tts_api_key is not None else "unavailable"
    return {"stt": stt, "tts": tts}
