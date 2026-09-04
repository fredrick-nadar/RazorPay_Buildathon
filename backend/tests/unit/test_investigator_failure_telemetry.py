"""Failed cases keep their partial work, and evidence must be case-bound.

REVIEW-007: a controlled failure after real work used to raise a bare
``ValueError``, so a run reported ``actual_providers=[]``,
``attempted_providers=[]``, ``total_retries=0`` and ``total_tool_calls=0``
although the model had answered and retried.

REVIEW-009: a successful ``get_rule_manifest`` call satisfied the evidence
gate, so a model could submit a final verdict having consulted only static
rule metadata.

Everything here is network-free and scripted. No key is needed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.ai.base import LLMResponse
from app.ai.chain import AIChain
from app.domain.enums import CaseStatus
from app.investigator.budgets import InvestigationBudget
from app.investigator.engine import investigate_cases
from app.investigator.failures import InvestigatorExecutionError
from app.investigator.llm_provider import LLMInvestigatorProvider
from app.investigator.tools import ToolDispatcher
from app.verifier.snapshot import build_evidence_snapshot
from tests.unit.test_investigator_engine import _make_duplicate_ledger_fixtures

SENTINEL = "gsk_sentinel_that_must_never_be_persisted"

ZERO_TOOL_FINAL = json.dumps(
    {
        "action": "final",
        "unresolved": {
            "reason_codes": ["NON_UNIQUE_EVIDENCE"],
            "missing_evidence": ["unique UTR"],
            "next_step": "manual review",
        },
    }
)


def _chain(turns: list[str]) -> AIChain:
    queue = list(turns)

    class Backend:
        provider_id = "scripted-groq"
        model = "scripted-model-1"

        def chat(
            self,
            system: str,
            user: str,
            json_mode: bool = False,
            timeout_s: float | None = None,
        ) -> LLMResponse:
            return LLMResponse(
                text=queue.pop(0),
                provider_id=self.provider_id,
                model=self.model,
                latency_ms=0.0,
            )

    return AIChain([Backend()])


def _fixtures() -> tuple[Any, Any, ToolDispatcher]:
    records, cases = _make_duplicate_ledger_fixtures()
    tools = ToolDispatcher(
        snapshot=build_evidence_snapshot(records),
        records=records,
        cases={case.case_id: case for case in cases},
        graph_json={},
    )
    return records, cases, tools


def _tool(name: str, **arguments: Any) -> str:
    return json.dumps({"action": "tool", "tool": name, "arguments": arguments})


class TestFailedCasesKeepTheirTelemetry:
    def test_completed_responses_then_schema_exhaustion_keep_provider_facts(self) -> None:
        """The exact REVIEW-007 reproduction, now reporting honestly."""
        records, cases, _tools = _fixtures()
        outcome = investigate_cases(
            records, cases, LLMInvestigatorProvider(_chain([ZERO_TOOL_FINAL] * 2))
        )
        summary = outcome.summary()

        # Two model turns completed, so the responder is named.
        assert summary["actual_providers"] == ["scripted-groq"]
        assert summary["attempted_providers"] == ["scripted-groq"]
        assert summary["total_retries"] == 2
        assert summary["investigation_failure_count"] == 1
        assert summary["fully_investigated"] is False

        entry = summary["cases"][0]
        assert entry["failure_code"] == "FINAL_WITHOUT_CASE_EVIDENCE"
        assert len(entry["provider_attempts"]) == 2
        assert {item["outcome"] for item in entry["provider_attempts"]} == {"SUCCESS"}

    def test_a_successful_evidence_call_then_a_failed_final_keeps_the_tool_count(
        self,
    ) -> None:
        records, cases, tools = _fixtures()
        case = cases[0]
        evidence = case.evidence[0]
        turns = [
            _tool("get_record", record_id=f"{evidence.record_type}:{evidence.record_id}"),
            # Two malformed finals exhaust the schema-retry budget.
            "not json at all",
            "still not json",
        ]
        provider = LLMInvestigatorProvider(_chain(turns))
        with pytest.raises(InvestigatorExecutionError) as excinfo:
            provider.investigate(case, tools, InvestigationBudget(), {})

        failure = excinfo.value
        assert failure.code == "MALFORMED_MODEL_JSON"
        # The tool call really happened and is not reported as zero.
        assert failure.tool_calls_used == 1
        assert failure.evidence_tool_calls == 1
        assert failure.retries_used == 2
        assert failure.actual_providers == ("scripted-groq",)
        assert failure.attempted_providers == ("scripted-groq",)

    def test_actual_providers_is_empty_only_when_nobody_answered(self) -> None:
        """The invariant must hold on the failure path too."""
        records, cases, _tools = _fixtures()

        class Dead:
            provider_id = "llm:scripted-dead"
            policy_fingerprint = "policy-test"

            def investigate(self, case: Any, tools: Any, budget: Any, context: Any) -> Any:
                raise InvestigatorExecutionError("PROVIDER_CHAIN_EXHAUSTED")

        summary = investigate_cases(records, cases, Dead()).summary()
        assert summary["actual_providers"] == []
        assert summary["attempted_providers"] == []
        assert summary["investigation_failure_count"] == 1

    def test_a_failed_case_gains_no_proof_correction_or_closure(self) -> None:
        records, cases, _tools = _fixtures()
        outcome = investigate_cases(
            records, cases, LLMInvestigatorProvider(_chain([ZERO_TOOL_FINAL] * 2))
        )
        for item in outcome.investigations:
            assert item.status == "FAILED"
            assert item.case.status == CaseStatus.INVESTIGATION_FAILED
            assert item.proof is None
            assert item.dry_run is None
            assert item.hypothesis is None
            assert item.verifier_result is None

    def test_persisted_failure_output_carries_no_secret_or_model_content(self) -> None:
        records, cases, tools = _fixtures()
        case = cases[0]
        turns = [
            _tool("get_case", case_id=case.case_id),
            json.dumps(
                {
                    "action": "final",
                    "unresolved": {
                        "reason_codes": ["NON_UNIQUE_EVIDENCE"],
                        "missing_evidence": ["x"],
                        "next_step": "review",
                        # A hostile echo plus a forbidden extra field.
                        "secret_echo": SENTINEL,
                    },
                }
            ),
        ] * 2
        provider = LLMInvestigatorProvider(_chain(turns))
        with pytest.raises(InvestigatorExecutionError) as excinfo:
            provider.investigate(case, tools, InvestigationBudget(), {})

        payload = json.dumps(excinfo.value.telemetry(), default=str)
        assert SENTINEL not in payload
        assert "gsk_" not in payload
        assert SENTINEL not in str(excinfo.value)
        # No prompt, response body, header or URL leaked into the telemetry.
        for banned in ("Authorization", "Bearer", "https://", "You are the bounded"):
            assert banned not in payload
        # Only typed keys are present.
        assert set(excinfo.value.telemetry()) == {
            "failure_code",
            "attempts",
            "attempted_providers",
            # Providers reached but never dialled are reported separately.
            "considered_providers",
            "skipped_providers",
            "actual_providers",
            "retries_used",
            "tool_calls_used",
            "evidence_tool_calls",
            "trace",
        }

    def test_the_run_summary_of_a_failed_case_holds_no_secret(self) -> None:
        records, cases, _tools = _fixtures()
        hostile = json.dumps(
            {
                "action": "final",
                "unresolved": {
                    "reason_codes": ["NON_UNIQUE_EVIDENCE"],
                    "missing_evidence": [SENTINEL],
                    "next_step": SENTINEL,
                },
            }
        )
        summary = investigate_cases(
            records, cases, LLMInvestigatorProvider(_chain([hostile] * 2))
        ).summary()
        payload = json.dumps(summary, default=str)
        assert SENTINEL not in payload
        assert "gsk_" not in payload


class TestEvidenceMustBeCaseBound:
    def test_get_case_then_final_is_accepted(self) -> None:
        records, cases, tools = _fixtures()
        case = cases[0]
        provider = LLMInvestigatorProvider(
            _chain([_tool("get_case", case_id=case.case_id), ZERO_TOOL_FINAL])
        )
        result = provider.investigate(case, tools, InvestigationBudget(), {})
        assert result.unresolved is not None
        assert result.evidence_tool_calls == 1

    def test_a_valid_get_record_then_final_is_accepted(self) -> None:
        records, cases, tools = _fixtures()
        case = cases[0]
        evidence = case.evidence[0]
        provider = LLMInvestigatorProvider(
            _chain(
                [
                    _tool(
                        "get_record",
                        record_id=f"{evidence.record_type}:{evidence.record_id}",
                    ),
                    ZERO_TOOL_FINAL,
                ]
            )
        )
        result = provider.investigate(case, tools, InvestigationBudget(), {})
        assert result.unresolved is not None
        assert result.evidence_tool_calls == 1

    def test_rule_manifest_only_then_final_is_rejected(self) -> None:
        """The REVIEW-009 loophole: static metadata is not case evidence."""
        records, cases, tools = _fixtures()
        case = cases[0]
        # Confirm the manifest call really does succeed, so the rejection is
        # about relevance rather than about a tool error.
        assert tools.dispatch("get_rule_manifest", {}).get("error") is None

        provider = LLMInvestigatorProvider(
            _chain([_tool("get_rule_manifest"), ZERO_TOOL_FINAL, ZERO_TOOL_FINAL])
        )
        with pytest.raises(InvestigatorExecutionError) as excinfo:
            provider.investigate(case, tools, InvestigationBudget(), {})
        assert excinfo.value.code == "FINAL_WITHOUT_CASE_EVIDENCE"
        # The call is still counted as a successful tool call, just not as
        # evidence for this case.
        assert excinfo.value.tool_calls_used == 1
        assert excinfo.value.evidence_tool_calls == 0

    def test_the_manifest_remains_callable_alongside_real_evidence(self) -> None:
        records, cases, tools = _fixtures()
        case = cases[0]
        provider = LLMInvestigatorProvider(
            _chain(
                [
                    _tool("get_rule_manifest"),
                    _tool("get_case", case_id=case.case_id),
                    ZERO_TOOL_FINAL,
                ]
            )
        )
        result = provider.investigate(case, tools, InvestigationBudget(), {})
        assert result.unresolved is not None
        assert result.tool_calls_used == 2
        assert result.evidence_tool_calls == 1

    def test_an_unrelated_record_then_final_is_rejected(self) -> None:
        records, cases, tools = _fixtures()
        case = cases[0]
        provider = LLMInvestigatorProvider(
            _chain(
                [
                    _tool("get_record", record_id="LEDGER_ENTRY:led_not_in_this_case"),
                    ZERO_TOOL_FINAL,
                    ZERO_TOOL_FINAL,
                ]
            )
        )
        with pytest.raises(InvestigatorExecutionError) as excinfo:
            provider.investigate(case, tools, InvestigationBudget(), {})
        assert excinfo.value.code == "FINAL_WITHOUT_CASE_EVIDENCE"
        assert excinfo.value.evidence_tool_calls == 0

    def test_an_errored_evidence_call_then_final_is_rejected(self) -> None:
        records, cases, tools = _fixtures()
        case = cases[0]
        provider = LLMInvestigatorProvider(
            _chain(
                [
                    # Right tool, unknown case: the dispatch errors.
                    _tool("get_case", case_id="case-does-not-exist"),
                    ZERO_TOOL_FINAL,
                    ZERO_TOOL_FINAL,
                ]
            )
        )
        with pytest.raises(InvestigatorExecutionError) as excinfo:
            provider.investigate(case, tools, InvestigationBudget(), {})
        assert excinfo.value.code == "FINAL_WITHOUT_CASE_EVIDENCE"
        assert excinfo.value.evidence_tool_calls == 0

    def test_a_forbidden_tool_then_final_is_rejected(self) -> None:
        records, cases, tools = _fixtures()
        case = cases[0]
        provider = LLMInvestigatorProvider(
            _chain(
                [
                    _tool("approve_correction", case_id=case.case_id),
                    ZERO_TOOL_FINAL,
                    ZERO_TOOL_FINAL,
                ]
            )
        )
        with pytest.raises(InvestigatorExecutionError) as excinfo:
            provider.investigate(case, tools, InvestigationBudget(), {})
        assert excinfo.value.code == "FINAL_WITHOUT_CASE_EVIDENCE"
        assert excinfo.value.evidence_tool_calls == 0

    def test_evidence_metadata_records_relevance_without_record_prose(self) -> None:
        records, cases, tools = _fixtures()
        case = cases[0]
        provider = LLMInvestigatorProvider(
            _chain([_tool("get_case", case_id=case.case_id), ZERO_TOOL_FINAL])
        )
        result = provider.investigate(case, tools, InvestigationBudget(), {})
        tool_steps = [step for step in result.trace if step.get("type") == "tool"]
        assert tool_steps
        step = tool_steps[0]
        # The relevance decision is recorded...
        assert step["case_evidence"] is True
        assert step["identifiers"]["case_id"] == case.case_id
        # ...but the observation content is not.
        payload = json.dumps(result.trace, default=str)
        assert case.summary not in payload
        assert set(step) == {
            "step",
            "type",
            "tool",
            "outcome",
            "result_keys",
            "identifiers",
            "case_evidence",
        }
