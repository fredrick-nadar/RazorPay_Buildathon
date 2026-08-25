"""Unit tests for the deterministic voice parser (PRD 13.5).

Covers all nine allowed intents across English and Hinglish paraphrases,
Indic number conversion to exact signed integer paise, case-ID extraction,
and the honest unknown-case path.
"""

from __future__ import annotations

import pytest

from app.voice.enums import VoiceIntent
from app.voice.parser import (
    classify_intent,
    extract_entities,
    normalize_transcript,
    parse_indian_amount_to_paise,
    requires_confirmation,
)


class TestAmountParsing:
    @pytest.mark.parametrize(
        ("text", "expected_paise"),
        [
            ("10 thousand rupees", 1_000_000),
            ("10 thousand", 1_000_000),
            ("10k", 1_000_000),
            ("5 lakh", 50_000_000),
            ("2 crore", 2_000_000_000),
            ("50 paise", 50),
            ("1,500.75", 150_075),
            ("15000", 1_500_000),
            ("100 rupees", 10_000),
            (
                "\u0926\u0938 \u0939\u091c\u093c\u093e\u0930 \u0930\u0941\u092a\u092f\u0947",
                1_000_000,
            ),
            ("\u0926\u0938 \u0939\u091c\u093e\u0930", 1_000_000),
            ("\u092a\u093e\u0901\u091a \u0932\u093e\u0916", 50_000_000),
            ("\u0967\u096e\u0966\u0966", 180_000),
            ("fifty thousand", 5_000_000),
            ("no amount here", None),
        ],
    )
    def test_amounts_convert_to_exact_paise(self, text: str, expected_paise: int | None) -> None:
        assert parse_indian_amount_to_paise(text) == expected_paise

    def test_devanagari_digits_normalized(self) -> None:
        assert normalize_transcript("\u0967\u096e\u0966\u0966") == "1800"


class TestAllowedIntents:
    @pytest.mark.parametrize(
        ("transcript", "expected"),
        [
            ("run reconciliation", VoiceIntent.RUN_RECONCILIATION),
            ("close today's batch", VoiceIntent.RUN_RECONCILIATION),
            ("aaj ka reconciliation chalao", VoiceIntent.RUN_RECONCILIATION),
            ("open presentation mode", VoiceIntent.OPEN_PRESENTATION_MODE),
            ("demo mode", VoiceIntent.OPEN_PRESENTATION_MODE),
            ("show case c9aa7339d62d", VoiceIntent.SHOW_CASE),
            ("open case 4f2b91c0aa17", VoiceIntent.SHOW_CASE),
            ("show unresolved cases", VoiceIntent.LIST_UNRESOLVED_CASES),
            ("list unresolved", VoiceIntent.LIST_UNRESOLVED_CASES),
            ("filter approval required cases", VoiceIntent.FILTER_CASES),
            ("show verified corrections below 10000", VoiceIntent.FILTER_CASES),
            ("why is case c9aa7339d62d unresolved", VoiceIntent.EXPLAIN_CASE),
            ("explain the case", VoiceIntent.EXPLAIN_CASE),
            ("what evidence is missing", VoiceIntent.SHOW_MISSING_EVIDENCE),
            ("missing evidence", VoiceIntent.SHOW_MISSING_EVIDENCE),
            (
                "prepare previews for the verified corrections",
                VoiceIntent.PREPARE_VERIFIED_CORRECTION_PREVIEWS,
            ),
            (
                "prepare verified correction previews",
                VoiceIntent.PREPARE_VERIFIED_CORRECTION_PREVIEWS,
            ),
            ("cancel", VoiceIntent.CANCEL_VOICE_REQUEST),
            ("never mind", VoiceIntent.CANCEL_VOICE_REQUEST),
        ],
    )
    def test_intent_classification(self, transcript: str, expected: VoiceIntent) -> None:
        assert classify_intent(transcript) is expected

    def test_confirmation_required_intents(self) -> None:
        assert requires_confirmation(VoiceIntent.RUN_RECONCILIATION)
        assert requires_confirmation(VoiceIntent.PREPARE_VERIFIED_CORRECTION_PREVIEWS)
        assert not requires_confirmation(VoiceIntent.SHOW_CASE)


class TestEntityExtraction:
    def test_case_id_extraction(self) -> None:
        assert extract_entities("show case c9aa7339d62d").case_id == "case-c9aa7339d62d"
        assert (
            extract_entities("why is case-4f2b91c0aa17 unresolved").case_id == "case-4f2b91c0aa17"
        )
        assert extract_entities("case_9f8e7d6c5b4a kyun").case_id == "case-9f8e7d6c5b4a"

    def test_plural_cases_is_not_a_case_id(self) -> None:
        assert extract_entities("show unresolved cases").case_id is None

    def test_spoken_numeric_reference_is_captured_honestly(self) -> None:
        entity = extract_entities("why is case 1 unresolved")
        assert entity.case_id == "case-1"

    def test_status_and_category(self) -> None:
        entity = extract_entities("filter duplicate cases with approval")
        assert entity.status == "APPROVAL_REQUIRED"
        assert entity.category == "DUPLICATE_LEDGER_POSTING"

    def test_amount_entity_in_filter(self) -> None:
        entity = extract_entities("prepare previews below 10000")
        assert entity.amount_paise == 1_000_000
