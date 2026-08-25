"""Voice safety guardrails (PRD 13.5.1).

The guardrail layer is the authority boundary for every voice command:
forbidden intents are detected before allowed classification, produce a
standardized localized refusal directing the controller to the visible
approval panel, and can never be executed. Defense in depth: the guardrail
check runs again at execution time on the canonical parse.

Localized refusal text exists for every Tier-1 language for the core
approval/apply refusal; other families fall back to English until their
per-language packs pass (languages are labelled honestly in /languages).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.voice.enums import (
    ForbiddenVoiceIntent,
    VoiceIntent,
    VoiceIntentCategory,
    VoiceLanguage,
)
from app.voice.parser import classify_forbidden, normalize_transcript

_CORE_REFUSAL: str = (
    "I cannot approve or apply financial corrections by voice. "
    "Review the proof package in the approval panel."
)

_CORE_REFUSAL_LOCALIZED: dict[str, str] = {
    "hi-IN": ("मैं आवाज़ से वित्तीय सुधार स्वीकृत या लागू नहीं कर सकता. अप्रूवल पैनल में प्रूफ़ पैकेज देखें."),
    "ta-IN": ("நான் குரல் வாயில் நிதித் திருத்தங்களை அனுமதிக்கவே அல்ல. அப்பிர்வல் பேனலில் சான்றித்தழை சரிபார்க்கவும்."),
    "te-IN": ("నేను వాయ్చ్ ద్వారా ఆర్థిక దిదర్లును ఆమోదించలేను. అప్రోవల్ ప్రమాణలో రుజువు చూడండి."),
    "kn-IN": (
        "ನಾನು ಧ್ವನಿಯಿಂದ ಆರ್ತಿಕ ತಿದ್ದತಿಗಳನ್ನು ಅಪ್ರೂವ್ ಅಥವಾ ಅನುಷ್ಠಾಪಿಸಲಾಗದು. ಅಪ್ರೂವಲ್ ಪ್ಯಾನಲ್ನಲ್ಲಿ ಪುರಾವಆವಾರ್ಡನ್ನು ನೋಡಿ."
    ),
}

_SPECIFIC_HINTS: dict[ForbiddenVoiceIntent, str] = {
    ForbiddenVoiceIntent.APPROVE_CORRECTION: (
        "Approve every correction through the visible approval panel."
    ),
    ForbiddenVoiceIntent.APPLY_CORRECTION: (
        "Simulated application happens only after approval in the UI."
    ),
    ForbiddenVoiceIntent.EDIT_IMPORTED_RECORD: (
        "Imported source rows are immutable and can never be edited."
    ),
    ForbiddenVoiceIntent.OVERRIDE_VERIFIER: ("Verifier PASS cannot be overridden by any channel."),
    ForbiddenVoiceIntent.MARK_RESOLVED: ("Cases resolve only through deterministic verification."),
    ForbiddenVoiceIntent.MOVE_MONEY: "ARGUS never moves real money.",
    ForbiddenVoiceIntent.CHANGE_AUTHORITY_POLICY: (
        "Authority policy changes are out of scope for voice."
    ),
    ForbiddenVoiceIntent.REVEAL_SECRET: ("Secrets are never revealed through any interface."),
}


@dataclass(frozen=True)
class VoiceClassification:
    """Outcome of the safety-first classification pass."""

    category: VoiceIntentCategory
    intent: VoiceIntent | None = None
    forbidden_intent: ForbiddenVoiceIntent | None = None
    reason_code: str = "OK"
    refusal: str | None = None


def refusal_message(forbidden: ForbiddenVoiceIntent, language: VoiceLanguage) -> str:
    """Localized refusal: core families get full translations, others English."""
    base = _CORE_REFUSAL_LOCALIZED.get(language.value)
    if base is None:
        base = _CORE_REFUSAL
    hint = _SPECIFIC_HINTS.get(forbidden)
    if forbidden in (
        ForbiddenVoiceIntent.APPROVE_CORRECTION,
        ForbiddenVoiceIntent.APPLY_CORRECTION,
    ):
        return base
    if language is VoiceLanguage.HI_IN:
        return (
            "\u092e\u0948\u0902 \u0906\u0935\u093e\u091c\u093c \u0938\u0947 "
            "\u092f\u0939 \u0928\u0939\u0940\u0902 \u0915\u0930 \u0938\u0915\u0924\u093e. "
            + str(base)
        )
    return f"{_CORE_REFUSAL} ({hint})"


def classify_command(transcript: str, language: VoiceLanguage) -> VoiceClassification:
    """Safety-first classification of one utterance."""
    normalized = normalize_transcript(transcript)
    forbidden = classify_forbidden(normalized)
    if forbidden is not None:
        return VoiceClassification(
            category=VoiceIntentCategory.FORBIDDEN,
            intent=None,
            forbidden_intent=forbidden,
            reason_code="FORBIDDEN_INTENT",
            refusal=refusal_message(forbidden, language),
        )
    from app.voice.parser import classify_intent

    intent = classify_intent(normalized)
    if intent is None:
        return VoiceClassification(
            category=VoiceIntentCategory.UNKNOWN,
            reason_code="NOT_UNDERSTOOD",
        )
    return VoiceClassification(
        category=VoiceIntentCategory.ALLOWED,
        intent=intent,
    )


__all__ = [
    "VoiceClassification",
    "classify_command",
    "refusal_message",
]
