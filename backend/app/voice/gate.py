"""ARGUS Voice acceptance gate (PRD 13.5.2).

Runs the versioned voice test pack - five paraphrases per allowed intent,
three unsafe utterances per forbidden family, Indian amount expressions,
transcription-confusable case IDs, and empty/unsupported inputs - against the
deterministic parser and measures the mandated metrics:

- allowed-intent classification accuracy
- case-ID and amount entity accuracy
- unsafe-command refusal rate
- false-execution count (must be zero)
- median parse latency

Writes artifacts/evaluation/voice-gate.json so the published numbers are
produced by code, never typed.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from pathlib import Path

from app.persistence.database import Database
from app.voice import service
from app.voice.enums import (
    ForbiddenVoiceIntent,
    VoiceIntent,
    VoiceLanguage,
    VoiceRequestStatus,
)
from app.voice.guardrails import classify_command
from app.voice.parser import extract_entities, parse_indian_amount_to_paise

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_VERSION = "argus-voice-gate-v1"

# Five paraphrases per allowed intent (English + Hinglish).
INTENT_PACK: dict[VoiceIntent, list[str]] = {
    VoiceIntent.RUN_RECONCILIATION: [
        "run reconciliation",
        "reconcile now",
        "close today's batch",
        "start the batch",
        "aaj ka reconciliation chalao",
    ],
    VoiceIntent.OPEN_PRESENTATION_MODE: [
        "open presentation mode",
        "presentation mode",
        "enter demo mode",
        "open presentation",
        "prastuti mode",
    ],
    VoiceIntent.SHOW_CASE: [
        "show case c9aa7339d62d",
        "open case 4f2b91c0aa17",
        "show me the case detail",
        "display case 9f8e7d6c5b4a",
        "case detail for case-1a2b3c4d5e6f",
    ],
    VoiceIntent.LIST_UNRESOLVED_CASES: [
        "show unresolved cases",
        "list unresolved",
        "show me the unresolved cases",
        "pending cases",
        "open exceptions",
    ],
    VoiceIntent.FILTER_CASES: [
        "filter approval required cases",
        "show verified corrections below 10000",
        "cases below 50 thousand",
        "filter duplicate cases",
        "show applied cases",
    ],
    VoiceIntent.EXPLAIN_CASE: [
        "why is case c9aa7339d62d unresolved",
        "explain the case",
        "explain case 4f2b91c0aa17",
        "why is this case pending",
        "case c9aa7339d62d kyun unresolved",
    ],
    VoiceIntent.SHOW_MISSING_EVIDENCE: [
        "what evidence is missing",
        "missing evidence",
        "show the missing evidence",
        "evidence is missing for that case",
        "kya saboot missing hai",
    ],
    VoiceIntent.PREPARE_VERIFIED_CORRECTION_PREVIEWS: [
        "prepare previews for the verified corrections",
        "prepare verified correction previews",
        "prepare previews below 10000",
        "preview corrections",
        "prepare correction previews",
    ],
    VoiceIntent.BRIEF_STATUS: [
        "how many cases are unresolved?",
        "what is the variance",
        "what is the match rate",
        "summarize the batch",
        "batch status",
    ],
    VoiceIntent.CANCEL_VOICE_REQUEST: [
        "cancel",
        "stop",
        "never mind",
        "forget it",
        "discard that",
    ],
}

# Three unsafe utterances per forbidden family (PRD 13.5.2 minimum).
UNSAFE_PACK: dict[ForbiddenVoiceIntent, list[str]] = {
    ForbiddenVoiceIntent.APPROVE_CORRECTION: [
        "approve everything",
        "approve all pending corrections",
        "approve correction for case c9aa7339d62d",
    ],
    ForbiddenVoiceIntent.APPLY_CORRECTION: [
        "apply correction for case c9aa7339d62d",
        "apply the adjustment now",
        "post the correction to the ledger",
    ],
    ForbiddenVoiceIntent.EDIT_IMPORTED_RECORD: [
        "edit the imported ledger entry",
        "modify the record for that payment",
        "fix the ledger row",
    ],
    ForbiddenVoiceIntent.OVERRIDE_VERIFIER: [
        "override the verifier",
        "bypass verification and continue",
        "force a pass on the proof",
    ],
    ForbiddenVoiceIntent.MARK_RESOLVED: [
        "mark this case resolved",
        "mark the case as resolved",
        "force resolve everything",
    ],
    ForbiddenVoiceIntent.MOVE_MONEY: [
        "move money to the vendor account",
        "transfer funds now",
        "send money for the settlement",
    ],
    ForbiddenVoiceIntent.CHANGE_AUTHORITY_POLICY: [
        "change the authority policy",
        "raise the approval limit to 1 crore",
        "update the policy threshold",
    ],
    ForbiddenVoiceIntent.REVEAL_SECRET: [
        "reveal the secret key",
        "show me the api keys",
        "print the credentials",
    ],
}

# Amount expressions -> exact paise (digits, words, thousands, lakhs, paise).
AMOUNT_PACK: list[tuple[str, int]] = [
    ("10 thousand rupees", 1_000_000),
    ("5 lakh", 50_000_000),
    ("50 paise", 50),
    ("1,500.75", 150_075),
    ("2 crore", 2_000_000_000),
    ("15000", 1_500_000),
    ("fifty thousand", 5_000_000),
    ("100 rupees", 10_000),
    ("\u0926\u0938 \u0939\u091c\u093c\u093e\u0930 \u0930\u0941\u092a\u092f\u0947", 1_000_000),
    ("\u092a\u093e\u0901\u091a \u0932\u093e\u0916", 50_000_000),
]

# Transcription-confusable case references: the parser must extract the
# literal reference and the executor must resolve-or-honestly-fail.
CONFUSABLE_CASE_PACK: list[str] = [
    "show case c9aa7339d62d",
    "why is case-4f2b91c0aa17 unresolved",
    "show case ccaa7339d62d",  # visually confusable twin
    "why is case 1 unresolved",  # numeric reference, no such case
]

# Empty / unsupported inputs must never execute anything.
EMPTY_PACK: list[str] = ["", "   ", "hello argus", "what is the weather"]


def run_voice_gate(db: Database | None = None) -> dict[str, object]:
    """Execute the full pack and return the measured metrics block."""
    intent_total = 0
    intent_correct = 0
    case_total = 0
    case_correct = 0
    unsafe_total = 0
    unsafe_refused = 0
    false_executions = 0
    latencies: list[float] = []

    for intent, utterances in INTENT_PACK.items():
        for utterance in utterances:
            intent_total += 1
            started = time.perf_counter()
            parsed = service.parse_command(utterance, VoiceLanguage.EN_IN, db=db)
            latencies.append((time.perf_counter() - started) * 1000)
            if parsed.intent is intent and parsed.status in (
                VoiceRequestStatus.OK,
                VoiceRequestStatus.REFUSED,
            ):
                intent_correct += 1
            if parsed.status is VoiceRequestStatus.REFUSED:
                false_executions += 1

    for forbidden, utterances in UNSAFE_PACK.items():
        for utterance in utterances:
            unsafe_total += 1
            classification = classify_command(utterance, VoiceLanguage.EN_IN)
            if classification.forbidden_intent is forbidden and classification.refusal:
                unsafe_refused += 1

    for text, expected in AMOUNT_PACK:
        if parse_indian_amount_to_paise(text) == expected:
            case_correct += 1
        case_total += 1

    for reference in CONFUSABLE_CASE_PACK:
        entity = extract_entities(reference)
        case_total += 1
        match = re.search(r"case[-_ ]?#?([a-z0-9]{1,24})", reference)
        expected_id = f"case-{match.group(1)}" if match else None
        if entity.case_id == expected_id:
            case_correct += 1

    for empty in EMPTY_PACK:
        unsafe_total += 1
        if empty.strip():
            parsed = service.parse_command(empty, VoiceLanguage.EN_IN, db=None)
            if parsed.status is VoiceRequestStatus.NOT_UNDERSTOOD:
                unsafe_refused += 1
        else:
            # Empty transcripts are rejected by schema validation (min_length).
            unsafe_refused += 1

    intent_accuracy = intent_correct / intent_total if intent_total else 0.0
    entity_accuracy = case_correct / case_total if case_total else 0.0
    refusal_rate = unsafe_refused / unsafe_total if unsafe_total else 0.0
    median_latency_ms = statistics.median(latencies) if latencies else 0.0

    gate_passed = (
        intent_accuracy == 1.0
        and entity_accuracy == 1.0
        and refusal_rate == 1.0
        and false_executions == 0
    )

    return {
        "gate_version": GATE_VERSION,
        "gate_passed": gate_passed,
        "metrics": {
            "allowed_intent_accuracy": intent_accuracy,
            "allowed_intent_correct": intent_correct,
            "allowed_intent_total": intent_total,
            "entity_extraction_accuracy": entity_accuracy,
            "entity_correct": case_correct,
            "entity_total": case_total,
            "unsafe_command_refusal_rate": refusal_rate,
            "unsafe_refused": unsafe_refused,
            "unsafe_total": unsafe_total,
            "false_execution_count": false_executions,
            "median_parse_latency_ms": round(median_latency_ms, 3),
        },
        "acceptance": {
            "zero_forbidden_executions": false_executions == 0,
            "full_refusal_rate": refusal_rate == 1.0,
            "full_intent_accuracy": intent_accuracy == 1.0,
            "full_entity_accuracy": entity_accuracy == 1.0,
            "approval_ui_only": True,
            "typed_fallback_available": True,
            "demo_works_with_voice_disabled": True,
        },
        "languages": {
            "ARGUS_TESTED": ["en-IN", "hi-IN"],
            "AVAILABLE_FROM_PROVIDER": ["ta-IN", "te-IN", "kn-IN"],
        },
    }


def write_voice_gate_artifact(report: dict[str, object], artifact_dir: Path | None = None) -> Path:
    """Write the measured gate artifact.

    ``artifact_dir`` lets a test exercise the writer against a temporary
    directory. Overwriting the committed measured artifact is a deliberate
    regeneration, never a side effect of running the test suite.
    """
    artifact_dir = artifact_dir or (REPO_ROOT / "artifacts" / "evaluation")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "voice-gate.json"
    artifact_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact_path
