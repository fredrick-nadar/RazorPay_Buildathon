"""Voice Control Layer domain vocabulary (PRD 13.5).

Voice is strictly an observational, navigational, and briefing surface.
The intent vocabulary below is the complete authority boundary: allowed
intents may read, filter, brief, navigate, or (with explicit confirmation)
trigger an idempotent batch run or display existing previews. Forbidden
intents can never be executed - they exist so the parser can name the
refusal reason precisely.
"""

from __future__ import annotations

from enum import StrEnum


class VoiceIntent(StrEnum):
    """Allowed voice intents (PRD 13.5.1). Every executor is read-only."""

    RUN_RECONCILIATION = "RUN_RECONCILIATION"
    OPEN_PRESENTATION_MODE = "OPEN_PRESENTATION_MODE"
    SHOW_CASE = "SHOW_CASE"
    LIST_UNRESOLVED_CASES = "LIST_UNRESOLVED_CASES"
    FILTER_CASES = "FILTER_CASES"
    EXPLAIN_CASE = "EXPLAIN_CASE"
    SHOW_MISSING_EVIDENCE = "SHOW_MISSING_EVIDENCE"
    PREPARE_VERIFIED_CORRECTION_PREVIEWS = "PREPARE_VERIFIED_CORRECTION_PREVIEWS"
    CANCEL_VOICE_REQUEST = "CANCEL_VOICE_REQUEST"
    # Read-only briefing extension: answers "how many cases", "what is the
    # variance", "batch status" deterministically from the latest run summary.
    # Observational only - no mutation capability is introduced.
    BRIEF_STATUS = "BRIEF_STATUS"


class ForbiddenVoiceIntent(StrEnum):
    """Prohibited voice commands. Detection produces an explicit refusal."""

    APPROVE_CORRECTION = "APPROVE_CORRECTION"
    APPLY_CORRECTION = "APPLY_CORRECTION"
    EDIT_IMPORTED_RECORD = "EDIT_IMPORTED_RECORD"
    OVERRIDE_VERIFIER = "OVERRIDE_VERIFIER"
    MARK_RESOLVED = "MARK_RESOLVED"
    MOVE_MONEY = "MOVE_MONEY"
    CHANGE_AUTHORITY_POLICY = "CHANGE_AUTHORITY_POLICY"
    REVEAL_SECRET = "REVEAL_SECRET"


class VoiceLanguage(StrEnum):
    """Supported recognition languages (PRD 13.5.3 Tier 1)."""

    EN_IN = "en-IN"
    HI_IN = "hi-IN"
    TA_IN = "ta-IN"
    TE_IN = "te-IN"
    KN_IN = "kn-IN"


class VoiceRequestStatus(StrEnum):
    """Lifecycle status of a parsed or executed voice command."""

    OK = "OK"
    REFUSED = "REFUSED"
    NOT_UNDERSTOOD = "NOT_UNDERSTOOD"
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"
    EXECUTED = "EXECUTED"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"


# Languages whose ARGUS-specific intent/entity/refusal packs have passed.
# Others remain provider-capability labels until their packs pass (PRD
# 13.5.3: never claim an untested language).
DEMO_READY_LANGUAGES: frozenset[str] = frozenset(
    {VoiceLanguage.EN_IN.value, VoiceLanguage.HI_IN.value}
)


class VoiceIntentCategory(StrEnum):
    """Coarse classification used by guardrails and tests."""

    ALLOWED = "ALLOWED"
    FORBIDDEN = "FORBIDDEN"
    UNKNOWN = "UNKNOWN"


__all__ = [
    "DEMO_READY_LANGUAGES",
    "ForbiddenVoiceIntent",
    "VoiceIntent",
    "VoiceIntentCategory",
    "VoiceLanguage",
    "VoiceRequestStatus",
]
