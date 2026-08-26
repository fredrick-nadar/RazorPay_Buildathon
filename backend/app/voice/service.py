"""Voice command service: parse -> guard -> execute -> audit (PRD 13.5).

Security model:
- Parsing happens server-side; the ONLY way to execute is the opaque token
  returned by /parse. Tokens are server-generated UUIDs bound to the canonical
  parse in a TTL cache, so a client can never submit a forged intent label.
- Guardrails re-run at execution time (defense in depth).
- Confirmation is required for RUN_RECONCILIATION and
  PREPARE_VERIFIED_CORRECTION_PREVIEWS (PRD 13.5.1).
- Every executed or refused command is appended to the audit log with a
  truncated transcript (sensitive-data minimization). No audio is retained.
- Integrates Sarvam AI (Saaras STT + Bulbul TTS) and ElevenLabs (Flash v2.5).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.audit.service import record_audit_event
from app.domain.enums import ActorType
from app.persistence.database import Database
from app.voice.conversational_agent import answer_custom_voice_query
from app.voice.elevenlabs_client import ElevenLabsClient
from app.voice.enums import (
    DEMO_READY_LANGUAGES,
    VoiceIntent,
    VoiceLanguage,
    VoiceRequestStatus,
)
from app.voice.executor import PRESENTATION_ROUTE, execute_intent
from app.voice.guardrails import VoiceClassification, classify_command
from app.voice.parser import extract_entities, normalize_transcript
from app.voice.sarvam_client import SarvamClient
from app.voice.schemas import (
    VoiceEntity,
    VoiceExecutionResult,
    VoiceLanguageInfo,
    VoiceParseResult,
)

_TOKEN_TTL_SECONDS = 300.0
_TOKENS: dict[str, tuple[VoiceIntent, VoiceEntity, VoiceLanguage, str, float]] = {}
_AUDITED_REFUSALS: set[str] = set()

_HELP_MESSAGE = (
    "Try: show unresolved cases, why is case <id> unresolved, "
    "prepare previews below 10000, run reconciliation, or open presentation mode."
)


def _get_sarvam_client() -> SarvamClient:
    return SarvamClient()


def _get_elevenlabs_client() -> ElevenLabsClient:
    return ElevenLabsClient()


def transcribe_voice_audio(
    audio_bytes: bytes,
    language_code: str = "unknown",
    content_type: str = "audio/wav",
) -> tuple[bool, str, str]:
    """Transcribe raw audio bytes using Sarvam Saaras v3."""
    sarvam = _get_sarvam_client()
    if sarvam.is_configured:
        res = sarvam.transcribe(audio_bytes, language_code=language_code, content_type=content_type)
        return res.success, res.transcript, res.reason
    return False, "", "SARVAM_API_KEY not configured"


def synthesize_voice_speech(
    text: str,
    language: VoiceLanguage = VoiceLanguage.EN_IN,
    provider: str = "auto",
    speaker: str | None = None,
) -> tuple[str | None, str, str]:
    """Synthesize speech audio into base64 string using Sarvam or ElevenLabs."""
    if not text.strip():
        return None, "audio/wav", "none"

    sarvam = _get_sarvam_client()
    elevenlabs = _get_elevenlabs_client()

    # If provider is explicitly specified
    if provider == "sarvam" and sarvam.is_configured:
        sarvam_res = sarvam.synthesize(text, target_language_code=language.value, speaker=speaker)
        if sarvam_res.success and sarvam_res.audio_base64:
            return sarvam_res.audio_base64, sarvam_res.content_type, "sarvam"

    if provider == "elevenlabs" and elevenlabs.is_configured:
        eleven_res = elevenlabs.synthesize(text)
        if eleven_res.success and eleven_res.audio_base64:
            return eleven_res.audio_base64, eleven_res.content_type, "elevenlabs"

    # Auto selection: Sarvam (Shubh Indic voice) first if configured, else ElevenLabs
    if sarvam.is_configured:
        sarvam_res = sarvam.synthesize(text, target_language_code=language.value, speaker=speaker)
        if sarvam_res.success and sarvam_res.audio_base64:
            return sarvam_res.audio_base64, sarvam_res.content_type, "sarvam"

    if elevenlabs.is_configured:
        eleven_res = elevenlabs.synthesize(text)
        if eleven_res.success and eleven_res.audio_base64:
            return eleven_res.audio_base64, eleven_res.content_type, "elevenlabs"

    return None, "audio/wav", "none"


def parse_command(
    transcript: str, language: VoiceLanguage, db: Database | None = None
) -> VoiceParseResult:
    """Classify one utterance; forbidden commands are refused and audited."""
    normalized = normalize_transcript(transcript)
    classification: VoiceClassification = classify_command(normalized, language)
    entities = extract_entities(normalized)

    if classification.category.value == "FORBIDDEN":
        msg = classification.refusal or "Voice command refused."
        audio_b64, ctype, _ = synthesize_voice_speech(msg, language)
        result = VoiceParseResult(
            token="",
            transcript=transcript[:280],
            language=language,
            status=VoiceRequestStatus.REFUSED,
            forbidden_intent=classification.forbidden_intent,
            entities=entities,
            message=msg,
            message_key="refused",
            audio_base64=audio_b64,
            content_type=ctype,
        )
        _audit(db, "VOICE_COMMAND_REFUSED", transcript, language, result, classification)
        return result

    # Informational, conversational, case explanations, and status queries
    # go directly to Gemini with live SQLite context
    action_intents = (
        VoiceIntent.RUN_RECONCILIATION,
        VoiceIntent.PREPARE_VERIFIED_CORRECTION_PREVIEWS,
        VoiceIntent.CANCEL_VOICE_REQUEST,
    )

    if classification.intent not in action_intents and db is not None:
        conv_answer, nav = answer_custom_voice_query(db, normalized, language)
        audio_b64, ctype, _ = synthesize_voice_speech(conv_answer, language)
        token = uuid.uuid4().hex
        _TOKENS[token] = (
            classification.intent or VoiceIntent.EXPLAIN_CASE,
            entities,
            language,
            normalized,
            time.monotonic() + _TOKEN_TTL_SECONDS,
        )
        res = VoiceParseResult(
            token=token,
            transcript=transcript[:280],
            language=language,
            status=VoiceRequestStatus.OK,
            intent=classification.intent or VoiceIntent.EXPLAIN_CASE,
            entities=entities,
            requires_confirmation=False,
            message=conv_answer,
            message_key="conversational_answer",
            audio_base64=audio_b64,
            content_type=ctype,
        )
        _audit(db, "VOICE_CONVERSATIONAL_QUERY", transcript, language, res, None)
        return res

    if classification.intent is None:
        return VoiceParseResult(
            token="",
            transcript=transcript[:280],
            language=language,
            status=VoiceRequestStatus.NOT_UNDERSTOOD,
            entities=entities,
            message=_HELP_MESSAGE,
            message_key="not_understood",
        )

    token = uuid.uuid4().hex
    _TOKENS[token] = (
        classification.intent,
        entities,
        language,
        normalized,
        time.monotonic() + _TOKEN_TTL_SECONDS,
    )
    return VoiceParseResult(
        token=token,
        transcript=transcript[:280],
        language=language,
        status=VoiceRequestStatus.OK,
        intent=classification.intent,
        entities=entities,
        requires_confirmation=classification.intent
        in (
            VoiceIntent.RUN_RECONCILIATION,
            VoiceIntent.PREPARE_VERIFIED_CORRECTION_PREVIEWS,
        ),
        message="Intent recognized. Confirm to execute."
        if classification.intent
        in (
            VoiceIntent.RUN_RECONCILIATION,
            VoiceIntent.PREPARE_VERIFIED_CORRECTION_PREVIEWS,
        )
        else "Intent recognized.",
        message_key="parsed",
    )


def execute_command(
    db: Database,
    token: str,
    confirmed: bool = False,
) -> VoiceExecutionResult:
    """Execute a server-parsed intent. Re-checks guardrails (defense in depth)."""
    entry = _TOKENS.get(token)
    if entry is None or entry[4] < time.monotonic():
        _TOKENS.pop(token, None)
        return VoiceExecutionResult(
            status=VoiceRequestStatus.ERROR,
            message_key="token_invalid",
            message="This voice request expired or was never parsed. Say the command again.",
            language=VoiceLanguage.EN_IN,
        )
    intent, entity, language, normalized, _ = entry

    forbidden = classify_command(normalized, language)
    if forbidden.category.value == "FORBIDDEN":
        msg = forbidden.refusal or "Voice command refused."
        audio_b64, ctype, _ = synthesize_voice_speech(msg, language)
        result = VoiceExecutionResult(
            status=VoiceRequestStatus.REFUSED,
            intent=None,
            message_key="refused",
            message=msg,
            language=language,
            audio_base64=audio_b64,
            content_type=ctype,
        )
        _audit(db, "VOICE_COMMAND_REFUSED", normalized, language, result, forbidden)
        return result

    if intent in (VoiceIntent.RUN_RECONCILIATION, VoiceIntent.PREPARE_VERIFIED_CORRECTION_PREVIEWS):
        if not confirmed:
            return VoiceExecutionResult(
                status=VoiceRequestStatus.REQUIRES_CONFIRMATION,
                intent=intent,
                message_key="requires_confirmation",
                message=(
                    "This will start a reconciliation batch on the dev dataset. Confirm to proceed."
                    if intent is VoiceIntent.RUN_RECONCILIATION
                    else "This will list verified correction previews. Confirm to proceed."
                ),
                language=language,
            )
        _TOKENS.pop(token, None)

    result = execute_intent(db, intent, entity, language)
    # Synthesize speech output with Sarvam or ElevenLabs
    if result.message:
        audio_b64, ctype, _ = synthesize_voice_speech(result.message, language)
        result = VoiceExecutionResult(
            status=result.status,
            intent=result.intent,
            message=result.message,
            message_key=result.message_key,
            language=result.language,
            cases=result.cases,
            previews=result.previews,
            briefing=result.briefing,
            navigation=result.navigation,
            run=result.run,
            audit_event_id=result.audit_event_id,
            audio_base64=audio_b64,
            content_type=ctype,
        )

    _audit(db, "VOICE_COMMAND_EXECUTED", normalized, language, result, None, intent=intent)
    return result


def _audit(
    db: Database | None,
    action: str,
    transcript: str,
    language: VoiceLanguage,
    result: Any,
    classification: Any,
    intent: VoiceIntent | None = None,
) -> None:
    if db is None:
        return
    payload: dict[str, Any] = {
        "transcript": transcript[:200],
        "language": language.value,
        "status": result.status.value if hasattr(result.status, "value") else str(result.status),
        "message_key": result.message_key,
    }
    if intent is not None:
        payload["intent"] = intent.value
    elif getattr(result, "forbidden_intent", None) is not None:
        payload["intent"] = str(result.forbidden_intent)
    if classification is not None and getattr(classification, "forbidden_intent", None) is not None:
        payload["forbidden_intent"] = classification.forbidden_intent.value
    record_audit_event(db=db, actor=ActorType.USER, action=action, payload=payload)


def languages() -> list[VoiceLanguageInfo]:
    """Honest per-language capability labels (PRD 13.5.3)."""
    labels = {
        VoiceLanguage.EN_IN: "English (India)",
        VoiceLanguage.HI_IN: "Hindi / Hinglish",
        VoiceLanguage.TA_IN: "தமிழ் (Tamil)",
        VoiceLanguage.TE_IN: "తెలుగు (Telugu)",
        VoiceLanguage.KN_IN: "ಕನ್ನಡ (Kannada)",
    }
    return [
        VoiceLanguageInfo(
            code=code,
            label=labels[code],
            tier=1,
            status="ARGUS_TESTED"
            if code.value in DEMO_READY_LANGUAGES
            else "AVAILABLE_FROM_PROVIDER",
        )
        for code in VoiceLanguage
    ]


__all__ = [
    "PRESENTATION_ROUTE",
    "execute_command",
    "languages",
    "parse_command",
    "synthesize_voice_speech",
    "transcribe_voice_audio",
]


def command(
    db: Database,
    transcript: str,
    language: VoiceLanguage = VoiceLanguage.EN_IN,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Atomic parse -> guard -> execute in ONE round trip (latency path).

    Returns a merged payload: parse fields plus `execution` when the intent
    ran. For confirmation-required intents executed without `confirmed`, the
    response carries `requires_confirmation` and a token for /voice/execute.
    """
    parsed = parse_command(transcript, language, db=db)
    base: dict[str, Any] = {
        "status": parsed.status.value,
        "intent": parsed.intent.value if parsed.intent else None,
        "forbidden_intent": parsed.forbidden_intent.value if parsed.forbidden_intent else None,
        "entities": parsed.entities.model_dump(),
        "message": parsed.message,
        "message_key": parsed.message_key,
        "requires_confirmation": parsed.requires_confirmation,
        "token": parsed.token,
        "transcript": parsed.transcript,
        "language": parsed.language.value,
        "execution": None,
    }
    if parsed.status in (VoiceRequestStatus.REFUSED, VoiceRequestStatus.NOT_UNDERSTOOD):
        return base
    if parsed.requires_confirmation and not confirmed:
        return base

    if parsed.message_key == "conversational_answer":
        base["execution"] = {
            "status": "OK",
            "intent": parsed.intent.value if parsed.intent else "EXPLAIN_CASE",
            "message": parsed.message,
            "message_key": "conversational_answer",
            "language": parsed.language.value,
            "cases": [],
            "previews": [],
            "briefing": None,
            "navigation": None,
            "audio_base64": parsed.audio_base64,
            "content_type": parsed.content_type,
        }
        return base

    executed = execute_command(db, parsed.token, confirmed=True)
    base["status"] = executed.status.value
    base["message"] = executed.message
    base["message_key"] = executed.message_key
    base["execution"] = executed.model_dump()
    if executed.status is not VoiceRequestStatus.EXECUTED:
        base["intent"] = executed.intent.value if executed.intent else base["intent"]
    return base


__all__ = [
    "command",
    "execute_command",
    "languages",
    "parse_command",
]
