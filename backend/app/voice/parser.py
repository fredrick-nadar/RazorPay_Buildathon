"""Deterministic voice intent parser and Indic number converter (PRD 13.5).

No model participates in parsing: classification is pure regex + keyword
matching over a normalized transcript, so behaviour is fully testable and
prompt-injection inert. Forbidden patterns are ALWAYS evaluated before
allowed patterns.

Amount handling converts Indian expressions - digits, "10 thousand",
"5 lakh", "2 crore", "50 paise", Devanagari digits and common Hindi words -
into exact signed integer paise. No float arithmetic ever participates.
"""

from __future__ import annotations

import re

from app.voice.enums import ForbiddenVoiceIntent, VoiceIntent
from app.voice.schemas import VoiceEntity

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_DEVANAGARI_DIGITS = str.maketrans(
    "\u0966\u0967\u0968\u0969\u096a\u096b\u096c\u096d\u096e\u096f",
    "0123456789",
)


def normalize_transcript(transcript: str) -> str:
    """Lowercase, unify digits, collapse whitespace; keep word characters."""
    text = transcript.translate(_DEVANAGARI_DIGITS).lower()
    text = re.sub(r"[^\w\s\u20b9.,\-\u0900-\u097F]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Amount parsing -> signed integer paise
# ---------------------------------------------------------------------------

_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
    "ek": 1,
    "do": 2,
    "teen": 3,
    "char": 4,
    "paanch": 5,
    "panch": 5,
    "das": 10,
    "bees": 20,
    "pachas": 50,
    "sau": 100,
}

_MULTIPLIERS: dict[str, int] = {
    "k": 1_000,
    "thousand": 1_000,
    "hazar": 1_000,
    "hazaar": 1_000,
    "\u0939\u091c\u093c\u093e\u0930": 1_000,
    "\u0939\u091c\u093e\u0930": 1_000,
    "lakh": 100_000,
    "lac": 100_000,
    "\u0932\u093e\u0916": 100_000,
    "crore": 10_000_000,
    "karod": 10_000_000,
    "\u0915\u0930\u094b\u0921\u093c": 10_000_000,
    "\u0915\u0930\u094b\u0921": 10_000_000,
}

_AMOUNT_RE = re.compile(
    r"(?:\u20b9|rs\.?|inr|rupees?|rupaye)?\s*"
    r"(?P<number>\d[\d,]*(?:\.\d+)?|[a-z]+(?:\s+[a-z]+)?)"
    r"\s*(?P<mult>k|thousand|hazar|hazaar|lakh|lac|crore)?"
    r"\s*(?P<paise>\bpaise\b)?"
)


def _word_number(token: str) -> int | None:
    """Compose simple English/Hinglish word numbers: 'ten', 'fifty thousand'."""
    parts = token.split()
    if not parts or len(parts) > 2:
        return None
    total = 0
    current = 0
    for part in parts:
        if part in _MULTIPLIERS:
            current = max(current, 1) * _MULTIPLIERS[part]
            total += current
            current = 0
            continue
        if part not in _WORDS:
            return None
        value = _WORDS[part]
        if value == 100:
            current = max(current, 1) * 100
        else:
            current += value
    return total + current


_HINDI_NUMBERS: dict[str, str] = {
    "\u090f\u0915": "1",
    "\u0926\u094b": "2",
    "\u0924\u0940\u0928": "3",
    "\u091a\u093e\u0930": "4",
    "\u092a\u093e\u0901\u091a": "5",
    "\u092a\u093e\u0902\u091a": "5",
    "\u0926\u0938": "10",
    "\u092c\u0940\u0938": "20",
    "\u092a\u091a\u093e\u0938": "50",
    "\u0938\u094c": "100",
}

_HINDI_MULTIPLIERS: dict[str, str] = {
    "\u0939\u091c\u093c\u093e\u0930": "thousand",
    "\u0939\u091c\u093e\u0930": "thousand",
    "\u0932\u093e\u0916": "lakh",
    "\u0915\u0930\u094b\u0921\u093c": "crore",
    "\u0915\u0930\u094b\u0921": "crore",
}


def _unify_hindi_words(text: str) -> str:
    """Rewrite Hindi number words to digits/multipliers before the regex pass."""
    for word, digit in _HINDI_NUMBERS.items():
        text = re.sub(rf"\b{word}\b", digit, text)
    for word, english in _HINDI_MULTIPLIERS.items():
        text = re.sub(rf"\b{word}\b", english, text)
    return text


def parse_indian_amount_to_paise(text: str) -> int | None:
    """Extract the first money expression from ``text`` as integer paise."""
    normalized = _unify_hindi_words(normalize_transcript(text))
    for match in _AMOUNT_RE.finditer(normalized):
        number_raw = match.group("number").replace(",", "")
        multiplier_raw = match.group("mult")
        is_paise = match.group("paise") is not None

        if re.fullmatch(r"\d+(?:\.\d+)?", number_raw):
            whole, _, fraction = number_raw.partition(".")
            units = int(whole) if whole else 0
            frac_paise = int((fraction + "00")[:2]) if fraction else 0
        else:
            composed = _word_number(number_raw)
            if composed is None:
                continue  # not a number word; keep scanning for a real amount
            units = composed
            frac_paise = 0

        if multiplier_raw:
            units *= _MULTIPLIERS[multiplier_raw]
            return units * 100 + frac_paise

        if is_paise:
            return units + frac_paise

        return units * 100 + frac_paise
    return None


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

_CASE_ID_RE = re.compile(r"\bcase(?!s)[-_\s?#]?([a-z0-9]{1,24})\b")
_STATUS_WORDS = {
    "unresolved": "UNRESOLVED",
    "approval": "APPROVAL_REQUIRED",
    "applied": "SIMULATED_APPLIED",
    "verified resolved": "VERIFIED_RESOLVED",
    "resolved": "VERIFIED_RESOLVED",
}
_CATEGORY_WORDS = {
    "duplicate": "DUPLICATE_LEDGER_POSTING",
    "refund": "MISSING_REFUND_POSTING",
    "timing": "SETTLEMENT_TIMING_WINDOW_SHIFT",
    "ambiguous": "AMBIGUOUS_EVIDENCE",
}


def extract_entities(transcript: str) -> VoiceEntity:
    """Extract case reference, amount (paise), status and category entities."""
    normalized = normalize_transcript(transcript)
    case_match = _CASE_ID_RE.search(normalized)
    spoken_ref = case_match.group(1) if case_match else None
    case_id = f"case-{spoken_ref}" if spoken_ref else None
    return VoiceEntity(
        case_id=case_id,
        spoken_case_ref=spoken_ref,
        amount_paise=parse_indian_amount_to_paise(normalized),
        status=next((v for k, v in _STATUS_WORDS.items() if k in normalized), None),
        category=next((v for k, v in _CATEGORY_WORDS.items() if k in normalized), None),
    )


# ---------------------------------------------------------------------------
# Forbidden patterns (always evaluated before allowed patterns)
# ---------------------------------------------------------------------------

_FORBIDDEN_PATTERNS: list[tuple[ForbiddenVoiceIntent, tuple[str, ...]]] = [
    (
        ForbiddenVoiceIntent.APPROVE_CORRECTION,
        (
            r"\bapprove\b",
            r"\bsanction\b",
            r"\bsign off\b",
            r"approve karo",
            r"manzuri",
            r"swikriti",
        ),
    ),
    (
        ForbiddenVoiceIntent.APPLY_CORRECTION,
        (
            r"apply (the )?(correction|adjustment|entry|delta)",
            r"post (the )?(correction|adjustment|entry)",
            r"ledger (update|write|mutation)",
            r"lagu karo",
            r"apply everything",
        ),
    ),
    (
        ForbiddenVoiceIntent.EDIT_IMPORTED_RECORD,
        (
            r"\bedit\b.*\b(record|entry|row|ledger|payment)\b",
            r"\bmodify\b.*\b(record|entry|row|ledger|payment)\b",
            r"change (the )?imported",
            r"fix the ledger",
        ),
    ),
    (
        ForbiddenVoiceIntent.OVERRIDE_VERIFIER,
        (
            r"override (the )?verif",
            r"bypass (the )?verif",
            r"force (a )?pass",
            r"skip (the )?verif",
        ),
    ),
    (
        ForbiddenVoiceIntent.MARK_RESOLVED,
        (
            r"mark (this |that |the )?(case |it )?(as )?resolved",
            r"mark .* resolved",
            r"force resolv",
        ),
    ),
    (
        ForbiddenVoiceIntent.MOVE_MONEY,
        (
            r"move money",
            r"transfer (money|funds|cash)",
            r"send (money|funds|payment)",
            r"wire (money|funds)",
            r"paisa bhejo",
            r"paise bhej",
        ),
    ),
    (
        ForbiddenVoiceIntent.CHANGE_AUTHORITY_POLICY,
        (
            r"change (the )?(authority )?policy",
            r"update (the )?policy",
            r"authority policy",
            r"(raise|increase) (the )?(approval )?(limit|threshold)",
        ),
    ),
    (
        ForbiddenVoiceIntent.REVEAL_SECRET,
        (
            r"reveal (the )?secret",
            r"show (me )?(the )?(api )?keys?",
            r"print (the )?credentials",
            r"\bpassword\b",
            r"api key",
        ),
    ),
]

# ---------------------------------------------------------------------------
# Allowed patterns
# ---------------------------------------------------------------------------

_ALLOWED_PATTERNS: list[tuple[VoiceIntent, tuple[str, ...]]] = [
    (
        VoiceIntent.CANCEL_VOICE_REQUEST,
        (
            r"\bcancel\b",
            r"\bstop\b",
            r"never ?mind",
            r"forget it",
            r"\bdiscard\b",
            r"\bruko\b",
        ),
    ),
    (
        VoiceIntent.OPEN_PRESENTATION_MODE,
        (
            r"presentation mode",
            r"open presentation",
            r"present(ation)? mode",
            r"demo mode",
            r"prastuti",
        ),
    ),
    (
        VoiceIntent.PREPARE_VERIFIED_CORRECTION_PREVIEWS,
        (
            r"prepare (verified )?(correction )?previews?",
            r"prepare previews",
            r"previews? for (the )?verified corrections",
            r"preview corrections",
        ),
    ),
    (
        VoiceIntent.EXPLAIN_CASE,
        (
            r"why (is|was) (the )?case",
            r"explain (the )?case",
            r"why .*(unresolved|pending)",
            r"case .*(kyun|kyon)",
        ),
    ),
    (
        VoiceIntent.SHOW_MISSING_EVIDENCE,
        (
            r"missing evidence",
            r"what evidence is missing",
            r"evidence is missing",
            r"kya saboot",
        ),
    ),
    (
        VoiceIntent.LIST_UNRESOLVED_CASES,
        (
            r"unresolved cases",
            r"list unresolved",
            r"show (me )?(the )?unresolved",
            r"pending cases",
            r"open exceptions",
        ),
    ),
    (
        VoiceIntent.SHOW_CASE,
        (
            r"show (me )?(the )?case",
            r"open case",
            r"display case",
            r"case detail",
        ),
    ),
    (
        VoiceIntent.RUN_RECONCILIATION,
        (
            r"run reconciliation",
            r"reconcile( now| the batch)?",
            r"close .{0,16}batch",
            r"(start|run) (a |the )?batch",
            r"reconciliation chalao",
            r"naya batch",
        ),
    ),
    (
        VoiceIntent.BRIEF_STATUS,
        (
            r"how many (cases|exceptions|records|unresolved)",
            r"what is the (variance|match rate|status)",
            r"\bmatch rate\b",
            r"summarize (the )?(batch|run)",
            r"(batch|run) status",
            r"give me a (summary|status)",
            r"(batch|run) ka (status|summary)",
        ),
    ),
    (
        VoiceIntent.FILTER_CASES,
        (
            r"\bfilter\b",
            r"(below|under|less than|above|over) .{0,4}[\d\u20b9]",
            r"cases? (below|under|over|above)",
            r"corrections? (below|under)",
            r"show .*(approval required|applied|resolved)",
        ),
    ),
]

_CONFIRMATION_REQUIRED: frozenset[VoiceIntent] = frozenset(
    {
        VoiceIntent.RUN_RECONCILIATION,
        VoiceIntent.PREPARE_VERIFIED_CORRECTION_PREVIEWS,
    }
)


def classify_forbidden(normalized: str) -> ForbiddenVoiceIntent | None:
    """Return the first forbidden intent matched; forbidden always wins."""
    for intent, patterns in _FORBIDDEN_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, normalized):
                return intent
    return None


def classify_intent(normalized: str) -> VoiceIntent | None:
    """Return the first allowed intent matched, in safety-first order."""
    for intent, patterns in _ALLOWED_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, normalized):
                return intent
    return None


def requires_confirmation(intent: VoiceIntent) -> bool:
    """Run reconciliation and preview preparation demand explicit confirm."""
    return intent in _CONFIRMATION_REQUIRED


def parse_transcript(transcript: str) -> tuple[VoiceIntent | None, VoiceEntity]:
    """One-pass parse: normalized transcript -> (intent, entities)."""
    normalized = normalize_transcript(transcript)
    intent = classify_intent(normalized)
    entities = extract_entities(normalized)
    return intent, entities


__all__ = [
    "classify_forbidden",
    "classify_intent",
    "extract_entities",
    "normalize_transcript",
    "parse_indian_amount_to_paise",
    "parse_transcript",
    "requires_confirmation",
]
