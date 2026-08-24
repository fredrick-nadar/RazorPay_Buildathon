"""Unit tests for Pydantic v2 validation schemas and extra='forbid' boundaries (PRD 10.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.enums import ExceptionCategory, ReasonCode
from app.investigator.schemas import (
    CompetingHypothesis,
    HypothesisOutput,
    HypothesisOutputModel,
    ProviderOutputModel,
    ProviderResult,
    UnresolvedExplanation,
    UnresolvedExplanationModel,
    convert_provider_output,
    normalise_reason_code,
)


def _valid_hypothesis_payload() -> dict[str, object]:
    return {
        "category": "DUPLICATE_LEDGER_POSTING",
        "claim": "two ledger rows post one source-side event",
        "evidence_ids": ["PAYMENT:pay-001", "LEDGER_ENTRY:led-001"],
        "competing_hypotheses": [
            {
                "category": "AMBIGUOUS_EVIDENCE",
                "why_possible": "could be distinct postings",
                "test_needed": "check source reference",
            }
        ],
        "known_uncertainty": ["must verify against snapshot"],
    }


def _valid_unresolved_payload() -> dict[str, object]:
    return {
        "reason_codes": ["NON_UNIQUE_EVIDENCE"],
        "missing_evidence": ["additional bank statement"],
        "next_step": "request manual review",
    }


def test_valid_hypothesis_output_parses() -> None:
    payload = _valid_hypothesis_payload()
    model = HypothesisOutputModel.model_validate(payload)
    assert model.category == "DUPLICATE_LEDGER_POSTING"
    assert len(model.evidence_ids) == 2
    assert len(model.competing_hypotheses) == 1


def test_valid_unresolved_explanation_parses() -> None:
    payload = _valid_unresolved_payload()
    model = UnresolvedExplanationModel.model_validate(payload)
    assert model.reason_codes == ["NON_UNIQUE_EVIDENCE"]
    assert model.missing_evidence == ["additional bank statement"]


def test_confidence_field_structurally_rejected() -> None:
    payload = _valid_hypothesis_payload()
    payload["confidence"] = 0.95
    with pytest.raises(ValidationError) as exc_info:
        HypothesisOutputModel.model_validate(payload)
    assert "extra_forbidden" in str(exc_info.value)


def test_score_field_structurally_rejected() -> None:
    payload = _valid_hypothesis_payload()
    payload["score"] = 42
    with pytest.raises(ValidationError) as exc_info:
        HypothesisOutputModel.model_validate(payload)
    assert "extra_forbidden" in str(exc_info.value)


def test_status_override_field_rejected() -> None:
    payload = _valid_hypothesis_payload()
    payload["status_override"] = "VERIFIED_RESOLVED"
    with pytest.raises(ValidationError) as exc_info:
        HypothesisOutputModel.model_validate(payload)
    assert "extra_forbidden" in str(exc_info.value)


def test_extra_field_in_competing_hypothesis_rejected() -> None:
    payload = _valid_hypothesis_payload()
    cast_competing = list(payload["competing_hypotheses"])  # type: ignore[arg-type]
    cast_competing[0] = {**cast_competing[0], "confidence": 0.8}
    payload["competing_hypotheses"] = cast_competing
    with pytest.raises(ValidationError) as exc_info:
        HypothesisOutputModel.model_validate(payload)
    assert "extra_forbidden" in str(exc_info.value)


def test_empty_evidence_ids_rejected() -> None:
    payload = _valid_hypothesis_payload()
    payload["evidence_ids"] = []
    with pytest.raises(ValidationError) as exc_info:
        HypothesisOutputModel.model_validate(payload)
    assert "evidence_ids must not be empty" in str(exc_info.value)


def test_malformed_evidence_id_rejected() -> None:
    payload = _valid_hypothesis_payload()
    payload["evidence_ids"] = ["malformed_evidence_id_without_colon"]
    with pytest.raises(ValidationError) as exc_info:
        HypothesisOutputModel.model_validate(payload)
    assert "must be TYPE:record_id format" in str(exc_info.value)


def test_unknown_category_rejected() -> None:
    payload = _valid_hypothesis_payload()
    payload["category"] = "INVALID_NONEXISTENT_CATEGORY"
    with pytest.raises(ValidationError) as exc_info:
        HypothesisOutputModel.model_validate(payload)
    assert "unknown category" in str(exc_info.value)


def test_no_competing_hypotheses_rejected() -> None:
    payload = _valid_hypothesis_payload()
    payload["competing_hypotheses"] = []
    with pytest.raises(ValidationError) as exc_info:
        HypothesisOutputModel.model_validate(payload)
    assert "at least one entry" in str(exc_info.value)


def test_provider_output_model_exactly_one_both_set_rejected() -> None:
    payload = {
        "hypothesis": _valid_hypothesis_payload(),
        "unresolved": _valid_unresolved_payload(),
    }
    with pytest.raises(ValidationError) as exc_info:
        ProviderOutputModel.model_validate(payload)
    assert "exactly one" in str(exc_info.value)


def test_provider_output_model_neither_set_rejected() -> None:
    payload = {"hypothesis": None, "unresolved": None}
    with pytest.raises(ValidationError) as exc_info:
        ProviderOutputModel.model_validate(payload)
    assert "exactly one" in str(exc_info.value)


def test_provider_output_model_extra_field_rejected() -> None:
    payload = {
        "hypothesis": _valid_hypothesis_payload(),
        "confidence": 0.99,
    }
    with pytest.raises(ValidationError) as exc_info:
        ProviderOutputModel.model_validate(payload)
    assert "extra_forbidden" in str(exc_info.value)


def test_reason_code_normalisation() -> None:
    # Known system code passes through unchanged
    assert (
        normalise_reason_code(ReasonCode.NON_UNIQUE_EVIDENCE.value)
        == ReasonCode.NON_UNIQUE_EVIDENCE.value
    )
    assert (
        normalise_reason_code(ReasonCode.UNKNOWN_EVIDENCE_ID.value)
        == ReasonCode.UNKNOWN_EVIDENCE_ID.value
    )

    # Unknown custom provider code is namespaced with PROVIDER:
    assert normalise_reason_code("CUSTOM_MERCHANT_ERROR") == "PROVIDER:CUSTOM_MERCHANT_ERROR"


def test_convert_provider_output_to_dataclasses() -> None:
    hyp_payload = _valid_hypothesis_payload()
    model = ProviderOutputModel.model_validate({"hypothesis": hyp_payload})
    result = convert_provider_output(model, tool_calls_used=3, retries_used=0)

    assert isinstance(result, ProviderResult)
    assert result.hypothesis is not None
    assert isinstance(result.hypothesis, HypothesisOutput)
    assert result.hypothesis.category == ExceptionCategory.DUPLICATE_LEDGER_POSTING
    assert isinstance(result.hypothesis.competing_hypotheses[0], CompetingHypothesis)
    assert result.unresolved is None
    assert result.tool_calls_used == 3
    assert result.retries_used == 0

    unr_payload = _valid_unresolved_payload()
    unr_payload["reason_codes"] = ["NON_UNIQUE_EVIDENCE", "UNEXPECTED_CUSTOM_REASON"]
    model2 = ProviderOutputModel.model_validate({"unresolved": unr_payload})
    result2 = convert_provider_output(model2, tool_calls_used=2, retries_used=1)

    assert isinstance(result2, ProviderResult)
    assert result2.hypothesis is None
    assert isinstance(result2.unresolved, UnresolvedExplanation)
    assert result2.unresolved.reason_codes == (
        "NON_UNIQUE_EVIDENCE",
        "PROVIDER:UNEXPECTED_CUSTOM_REASON",
    )
    assert result2.tool_calls_used == 2
    assert result2.retries_used == 1
