"""Case-evidence binding is tool-specific, not generic (REVIEW-013).

The reproduction: ``calculate_control_totals`` ignores every argument and
returns GLOBAL run totals, yet a call carrying the active ``case_id`` satisfied
the evidence gate because the old rule scanned any ``*_id`` field it found.

These tests run against the REAL ``ToolDispatcher``, because the unit fixture
in ``test_llm_provider`` stubs several handlers and would not reproduce the
behaviour of the shipped ones. Everything is network-free.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.ai.base import LLMResponse
from app.ai.chain import AIChain
from app.domain.enums import CaseStatus, ExceptionCategory
from app.domain.records import AcceptedRecords
from app.investigator.budgets import InvestigationBudget
from app.investigator.evidence_binding import (
    CASE_IDENTITY_TOOLS,
    EVIDENCE_BEARING_TOOLS,
    CaseEvidenceIndex,
    build_case_evidence_index,
    is_case_evidence_call,
)
from app.investigator.failures import InvestigatorExecutionError
from app.investigator.llm_provider import LLMInvestigatorProvider
from app.investigator.tools import ToolDispatcher
from app.reconciliation.detectors import CaseEvidence, CaseRecord
from app.verifier.snapshot import build_evidence_snapshot
from tests.unit.recon_fixtures import payment, refund
from tests.unit.test_investigator_engine import _make_duplicate_ledger_fixtures

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


@pytest.fixture
def bench() -> tuple[CaseRecord, ToolDispatcher, str]:
    """The real dispatcher over real fixtures, plus one cited evidence id."""
    records, cases = _make_duplicate_ledger_fixtures()
    case = cases[0]
    tools = ToolDispatcher(
        snapshot=build_evidence_snapshot(records),
        records=records,
        cases={item.case_id: item for item in cases},
        graph_json={
            "nodes": [{"id": f"{case.evidence[0].record_type}:{case.evidence[0].record_id}"}],
            "edges": [],
        },
    )
    cited = f"{case.evidence[0].record_type}:{case.evidence[0].record_id}"
    return case, tools, cited


def _decide(
    case: CaseRecord, tools: ToolDispatcher, tool: str, arguments: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
    observation = tools.dispatch(tool, arguments)
    verdict = is_case_evidence_call(
        case, tool, arguments, observation, build_case_evidence_index(case)
    )
    return verdict, observation


class TestAcceptedBindings:
    def test_valid_get_case_is_accepted(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, _cited = bench
        verdict, observation = _decide(case, tools, "get_case", {"case_id": case.case_id})
        assert observation.get("error") is None
        assert verdict is True

    def test_a_cited_get_record_is_accepted(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, cited = bench
        verdict, observation = _decide(case, tools, "get_record", {"record_id": cited})
        assert observation.get("error") is None
        assert verdict is True

    def test_a_cited_get_records_batch_is_accepted(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, cited = bench
        verdict, observation = _decide(case, tools, "get_records", {"record_ids": [cited]})
        assert observation.get("error") is None
        assert verdict is True

    def test_a_case_bound_date_window_calculation_is_accepted(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, cited = bench
        verdict, observation = _decide(
            case, tools, "check_date_window", {"record_ids": [cited], "rule_id": "R-WINDOW"}
        )
        assert observation.get("error") is None
        assert verdict is True

    def test_a_case_bound_identity_check_is_accepted(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, cited = bench
        verdict, _observation = _decide(
            case, tools, "check_unique_identity", {"record_ids": [cited], "rule_id": "R-UNIQUE"}
        )
        assert verdict is True

    def test_a_case_bound_expected_net_is_accepted(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, cited = bench
        payment_id = cited.split(":", 1)[1]
        verdict, observation = _decide(
            case,
            tools,
            "calculate_expected_net",
            {"payment_ids": [payment_id], "refund_ids": []},
        )
        assert observation.get("error") is None
        assert verdict is True

    def test_an_evidence_graph_naming_the_case_is_accepted(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, _cited = bench
        verdict, _observation = _decide(
            case, tools, "get_evidence_graph", {"case_id": case.case_id}
        )
        assert verdict is True


class TestRejectedBindings:
    def test_manifest_only_is_rejected(self, bench: tuple[CaseRecord, ToolDispatcher, str]) -> None:
        case, tools, _cited = bench
        verdict, observation = _decide(case, tools, "get_rule_manifest", {})
        # The call SUCCEEDS; it simply proves nothing about this case.
        assert observation.get("error") is None
        assert verdict is False
        assert "get_rule_manifest" not in EVIDENCE_BEARING_TOOLS

    def test_control_totals_with_an_injected_active_case_id_is_rejected(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        """The exact REVIEW-013 reproduction."""
        case, tools, _cited = bench
        verdict, observation = _decide(
            case, tools, "calculate_control_totals", {"case_id": case.case_id}
        )
        # It succeeds and returns GLOBAL run totals, ignoring its arguments.
        assert observation.get("error") is None
        assert "totals" in observation
        assert verdict is False
        assert "calculate_control_totals" not in EVIDENCE_BEARING_TOOLS

    def test_expected_net_with_an_injected_case_id_and_empty_lists_is_rejected(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, _cited = bench
        verdict, observation = _decide(
            case,
            tools,
            "calculate_expected_net",
            {"case_id": case.case_id, "payment_ids": [], "refund_ids": []},
        )
        assert observation.get("error") is None
        assert verdict is False

    def test_unrelated_ids_plus_an_injected_active_case_id_are_rejected(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, _cited = bench
        verdict, observation = _decide(
            case,
            tools,
            "check_unique_identity",
            {"record_ids": ["LEDGER_ENTRY:led_not_in_case"], "case_id": case.case_id},
        )
        assert observation.get("error") is None
        assert verdict is False

    def test_a_date_window_over_unrelated_records_is_rejected(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, _cited = bench
        verdict, _observation = _decide(
            case,
            tools,
            "check_date_window",
            {"record_ids": ["PAYMENT:pay_not_in_case"], "case_id": case.case_id},
        )
        assert verdict is False

    def test_an_unrelated_get_record_is_rejected(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, _cited = bench
        verdict, observation = _decide(
            case, tools, "get_record", {"record_id": "PAYMENT:pay_not_in_case"}
        )
        assert observation.get("error") == "UNKNOWN_EVIDENCE_ID"
        assert verdict is False

    def test_candidate_lookup_with_an_injected_case_id_is_rejected(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        """Its handler consumes only record_type, so there is no binding."""
        case, tools, _cited = bench
        verdict, observation = _decide(
            case,
            tools,
            "list_candidate_records",
            {"record_type": "LEDGER_ENTRY", "case_id": case.case_id},
        )
        assert observation.get("error") is None
        assert verdict is False
        assert "list_candidate_records" not in EVIDENCE_BEARING_TOOLS

    def test_a_get_case_for_another_case_is_rejected(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, _cited = bench
        verdict, observation = _decide(case, tools, "get_case", {"case_id": "case-does-not-exist"})
        assert observation.get("error") == "UNKNOWN_CASE"
        assert verdict is False

    def test_an_evidence_graph_for_another_case_is_rejected(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, _cited = bench
        verdict, _observation = _decide(
            case, tools, "get_evidence_graph", {"case_id": "case-does-not-exist"}
        )
        assert verdict is False

    def test_a_forbidden_tool_is_rejected(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, _cited = bench
        for forbidden in ("approve_correction", "apply_correction", "mark_resolved"):
            verdict, observation = _decide(case, tools, forbidden, {"case_id": case.case_id})
            assert observation.get("error") == "UNKNOWN_TOOL"
            assert verdict is False


class TestTheGateInTheRealLoop:
    """End-to-end through the provider, using the real dispatcher."""

    @staticmethod
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

    @staticmethod
    def _tool(name: str, **arguments: Any) -> str:
        return json.dumps({"action": "tool", "tool": name, "arguments": arguments})

    def test_control_totals_then_final_is_refused(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, _cited = bench
        provider = LLMInvestigatorProvider(
            self._chain(
                [
                    self._tool("calculate_control_totals", case_id=case.case_id),
                    ZERO_TOOL_FINAL,
                    ZERO_TOOL_FINAL,
                ]
            )
        )
        with pytest.raises(InvestigatorExecutionError) as excinfo:
            provider.investigate(case, tools, InvestigationBudget(), {})
        assert excinfo.value.code == "FINAL_WITHOUT_CASE_EVIDENCE"
        # The call still counted as a successful tool call, just not evidence.
        assert excinfo.value.tool_calls_used == 1
        assert excinfo.value.evidence_tool_calls == 0

    def test_a_cited_record_then_final_is_accepted(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, cited = bench
        provider = LLMInvestigatorProvider(
            self._chain([self._tool("get_record", record_id=cited), ZERO_TOOL_FINAL])
        )
        result = provider.investigate(case, tools, InvestigationBudget(), {})
        assert result.unresolved is not None
        assert result.evidence_tool_calls == 1

    def test_the_trace_records_relevance_without_record_prose(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, cited = bench
        provider = LLMInvestigatorProvider(
            self._chain(
                [
                    self._tool("calculate_control_totals", case_id=case.case_id),
                    self._tool("get_record", record_id=cited),
                    ZERO_TOOL_FINAL,
                ]
            )
        )
        result = provider.investigate(case, tools, InvestigationBudget(), {})
        steps = [step for step in result.trace if step.get("type") == "tool"]
        assert [step["tool"] for step in steps] == [
            "calculate_control_totals",
            "get_record",
        ]
        # The relevance decision is recorded per call...
        assert [step["case_evidence"] for step in steps] == [False, True]
        # ...and the observation content is not.
        payload = json.dumps(result.trace, default=str)
        assert case.summary not in payload
        for step in steps:
            assert set(step) == {
                "step",
                "type",
                "tool",
                "outcome",
                "result_keys",
                "identifiers",
                "case_evidence",
            }


# ---------------------------------------------------------------------------
# REVIEW-015: case identity and typed record identity are separate contracts.
# ---------------------------------------------------------------------------

SHARED_ID = "shared-001"


@pytest.fixture
def collision() -> tuple[CaseRecord, ToolDispatcher, CaseEvidenceIndex]:
    """A case citing PAYMENT:shared-001 while REFUND:shared-001 also exists.

    Both records resolve, so the handler succeeds for either spelling. Only the
    binding rules can tell them apart, which is what makes this fixture prove
    type discipline rather than accidental handler errors.
    """
    paid = payment(SHARED_ID, gross=100_000, fee=0, tax=0)
    refunded = refund(SHARED_ID, payment_id=SHARED_ID, amount=25_000)
    records = AcceptedRecords(
        payments=(paid,),
        refunds=(refunded,),
        settlements=(),
        bank_entries=(),
        ledger_entries=(),
    )
    case = CaseRecord(
        case_id="case-collision-01",
        category=ExceptionCategory.AMBIGUOUS_EVIDENCE,
        status=CaseStatus.UNRESOLVED,
        variance_paise=25_000,
        affected_amount_paise=25_000,
        proposed_delta_paise=None,
        currency="INR",
        summary="bare identifier shared across two record types",
        reason_codes=("NON_UNIQUE_EVIDENCE",),
        evidence=(CaseEvidence("PAYMENT", SHARED_ID),),
    )
    tools = ToolDispatcher(
        snapshot=build_evidence_snapshot(records),
        records=records,
        cases={case.case_id: case},
        graph_json={},
    )
    return case, tools, build_case_evidence_index(case)


class TestCaseIdCannotSpoofRecordEvidence:
    """REVIEW-015: the active case id may satisfy only ``get_case``."""

    def test_the_index_keeps_case_identity_out_of_the_record_sets(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, _tools, _cited = bench
        index = build_case_evidence_index(case)
        assert index.case_id == case.case_id
        assert index.case_id not in index.typed_records
        assert index.case_id not in index.payment_ids
        assert index.case_id not in index.refund_ids
        # Typed records are exact canonical pairs.
        assert all(":" in identifier for identifier in index.typed_records)

    def test_unique_identity_with_the_case_id_succeeds_but_is_not_evidence(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        """The exact REVIEW-015 reproduction."""
        case, tools, _cited = bench
        arguments = {"record_ids": [case.case_id]}
        observation = tools.dispatch("check_unique_identity", arguments)
        # The tool call genuinely SUCCEEDS ...
        assert observation.get("error") is None
        assert observation["unique_count"] == 1
        assert observation["is_unique"] is True
        # ... but validates no financial record, so it is not case evidence.
        assert is_case_evidence_call(case, "check_unique_identity", arguments, observation) is False

    @pytest.mark.parametrize(
        "tool,arguments_key",
        [
            ("check_unique_identity", "record_ids"),
            ("check_date_window", "record_ids"),
            ("get_record", "record_id"),
            ("get_records", "record_ids"),
        ],
    )
    def test_no_record_tool_accepts_the_bare_case_id(
        self,
        bench: tuple[CaseRecord, ToolDispatcher, str],
        tool: str,
        arguments_key: str,
    ) -> None:
        case, tools, _cited = bench
        value: Any = case.case_id if arguments_key == "record_id" else [case.case_id]
        arguments = {arguments_key: value}
        observation = tools.dispatch(tool, arguments)
        assert is_case_evidence_call(case, tool, arguments, observation) is False

    def test_expected_net_never_accepts_the_case_id_in_either_field(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, _cited = bench
        for arguments in (
            {"payment_ids": [case.case_id], "refund_ids": []},
            {"payment_ids": [], "refund_ids": [case.case_id]},
        ):
            observation = tools.dispatch("calculate_expected_net", arguments)
            assert (
                is_case_evidence_call(case, "calculate_expected_net", arguments, observation)
                is False
            )

    def test_get_case_with_the_exact_active_case_id_remains_accepted(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, _cited = bench
        arguments = {"case_id": case.case_id}
        observation = tools.dispatch("get_case", arguments)
        assert observation.get("error") is None
        assert is_case_evidence_call(case, "get_case", arguments, observation) is True
        assert "get_case" in CASE_IDENTITY_TOOLS


class TestTypedRecordIdentityIsPreserved:
    """REVIEW-015: PAYMENT:x and REFUND:x are never interchangeable."""

    def test_a_wrong_type_record_that_resolves_is_still_rejected(
        self, collision: tuple[CaseRecord, ToolDispatcher, CaseEvidenceIndex]
    ) -> None:
        """The decisive case: the handler SUCCEEDS for the wrong type."""
        case, tools, index = collision
        assert f"PAYMENT:{SHARED_ID}" in index.typed_records
        assert f"REFUND:{SHARED_ID}" not in index.typed_records

        arguments = {"record_id": f"REFUND:{SHARED_ID}"}
        observation = tools.dispatch("get_record", arguments)
        # The refund really exists, so there is no handler error to hide behind.
        assert observation.get("error") is None
        assert observation["refund_id"] == SHARED_ID
        assert is_case_evidence_call(case, "get_record", arguments, observation, index) is False

    def test_the_exact_cited_typed_record_remains_accepted(
        self, collision: tuple[CaseRecord, ToolDispatcher, CaseEvidenceIndex]
    ) -> None:
        case, tools, index = collision
        arguments = {"record_id": f"PAYMENT:{SHARED_ID}"}
        observation = tools.dispatch("get_record", arguments)
        assert observation.get("error") is None
        assert is_case_evidence_call(case, "get_record", arguments, observation, index) is True

    @pytest.mark.parametrize("tool", ["check_unique_identity", "check_date_window"])
    def test_identity_and_window_checks_reject_the_wrong_type(
        self,
        collision: tuple[CaseRecord, ToolDispatcher, CaseEvidenceIndex],
        tool: str,
    ) -> None:
        case, tools, index = collision
        arguments = {"record_ids": [f"REFUND:{SHARED_ID}"], "rule_id": "R-TEST"}
        observation = tools.dispatch(tool, arguments)
        assert is_case_evidence_call(case, tool, arguments, observation, index) is False

    @pytest.mark.parametrize("tool", ["check_unique_identity", "check_date_window"])
    def test_identity_and_window_checks_accept_the_cited_type(
        self,
        collision: tuple[CaseRecord, ToolDispatcher, CaseEvidenceIndex],
        tool: str,
    ) -> None:
        case, tools, index = collision
        arguments = {"record_ids": [f"PAYMENT:{SHARED_ID}"], "rule_id": "R-TEST"}
        observation = tools.dispatch(tool, arguments)
        assert observation.get("error") is None
        assert is_case_evidence_call(case, tool, arguments, observation, index) is True

    def test_a_bare_id_without_its_type_prefix_is_rejected(
        self, collision: tuple[CaseRecord, ToolDispatcher, CaseEvidenceIndex]
    ) -> None:
        """Record tools require the canonical TYPE:record_id spelling."""
        case, tools, index = collision
        arguments = {"record_ids": [SHARED_ID], "rule_id": "R-TEST"}
        observation = tools.dispatch("check_unique_identity", arguments)
        assert observation.get("error") is None
        assert (
            is_case_evidence_call(case, "check_unique_identity", arguments, observation, index)
            is False
        )


class TestExpectedNetIsFieldSpecific:
    """REVIEW-015: payment_ids and refund_ids are validated by their own type."""

    def test_a_cited_payment_id_qualifies_only_under_payment_ids(
        self, collision: tuple[CaseRecord, ToolDispatcher, CaseEvidenceIndex]
    ) -> None:
        case, tools, index = collision
        assert index.payment_ids == frozenset({SHARED_ID})
        assert index.refund_ids == frozenset()

        accepted = {"payment_ids": [SHARED_ID], "refund_ids": []}
        observation = tools.dispatch("calculate_expected_net", accepted)
        assert observation.get("error") is None
        assert (
            is_case_evidence_call(case, "calculate_expected_net", accepted, observation, index)
            is True
        )

        # The SAME bare id under refund_ids resolves (the refund exists) but is
        # not cited REFUND evidence for this case, so it cannot qualify.
        crossed = {"payment_ids": [], "refund_ids": [SHARED_ID]}
        crossed_observation = tools.dispatch("calculate_expected_net", crossed)
        assert crossed_observation.get("error") is None
        assert (
            is_case_evidence_call(
                case, "calculate_expected_net", crossed, crossed_observation, index
            )
            is False
        )

    def test_an_empty_calculation_never_qualifies(
        self, collision: tuple[CaseRecord, ToolDispatcher, CaseEvidenceIndex]
    ) -> None:
        case, tools, index = collision
        arguments = {"payment_ids": [], "refund_ids": []}
        observation = tools.dispatch("calculate_expected_net", arguments)
        assert observation.get("error") is None
        assert (
            is_case_evidence_call(case, "calculate_expected_net", arguments, observation, index)
            is False
        )


class TestEvidenceGraphUsesExactIdentifiers:
    """REVIEW-015: graph relevance comes from exact node identifiers."""

    @staticmethod
    def _dispatcher(case: CaseRecord, graph: dict[str, Any]) -> ToolDispatcher:
        records, cases = _make_duplicate_ledger_fixtures()
        return ToolDispatcher(
            snapshot=build_evidence_snapshot(records),
            records=records,
            cases={item.case_id: item for item in cases},
            graph_json=graph,
        )

    def test_an_exact_case_node_is_accepted(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, _tools, _cited = bench
        # app.graph.evidence emits CASE:<case_id> for case nodes.
        tools = self._dispatcher(case, {"nodes": [{"node_id": f"CASE:{case.case_id}"}]})
        arguments = {"case_id": case.case_id}
        observation = tools.dispatch("get_evidence_graph", arguments)
        assert is_case_evidence_call(case, "get_evidence_graph", arguments, observation) is True

    def test_an_exact_typed_cited_record_node_is_accepted(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, _tools, cited = bench
        tools = self._dispatcher(case, {"nodes": [{"node_id": cited}]})
        arguments = {"case_id": case.case_id}
        observation = tools.dispatch("get_evidence_graph", arguments)
        assert is_case_evidence_call(case, "get_evidence_graph", arguments, observation) is True

    @pytest.mark.parametrize(
        "graph",
        [
            {},
            {"nodes": []},
            {"nodes": [{"node_id": "PAYMENT:pay-unrelated"}]},
            # A bare id without its type prefix must not match.
            {"nodes": [{"node_id": "pay-001"}]},
            # The case id without the CASE: prefix must not match.
            {"nodes": [{"node_id": "case-dup-01"}]},
        ],
    )
    def test_unrelated_or_untyped_graph_identifiers_are_rejected(
        self, bench: tuple[CaseRecord, ToolDispatcher, str], graph: dict[str, Any]
    ) -> None:
        case, _tools, _cited = bench
        tools = self._dispatcher(case, graph)
        arguments = {"case_id": case.case_id}
        observation = tools.dispatch("get_evidence_graph", arguments)
        assert is_case_evidence_call(case, "get_evidence_graph", arguments, observation) is False

    def test_a_graph_request_for_another_case_is_rejected(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, _tools, _cited = bench
        tools = self._dispatcher(case, {"nodes": [{"node_id": f"CASE:{case.case_id}"}]})
        arguments = {"case_id": "case-someone-else"}
        observation = tools.dispatch("get_evidence_graph", arguments)
        assert is_case_evidence_call(case, "get_evidence_graph", arguments, observation) is False


class TestTheSpoofFailsInTheRealProviderLoop:
    """REVIEW-015 end-to-end through the real provider and dispatcher."""

    @staticmethod
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

    @staticmethod
    def _tool(name: str, **arguments: Any) -> str:
        return json.dumps({"action": "tool", "tool": name, "arguments": arguments})

    def test_the_case_id_spoof_then_final_fails_without_case_evidence(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, _cited = bench
        provider = LLMInvestigatorProvider(
            self._chain(
                [
                    self._tool("check_unique_identity", record_ids=[case.case_id]),
                    ZERO_TOOL_FINAL,
                    ZERO_TOOL_FINAL,
                ]
            )
        )
        with pytest.raises(InvestigatorExecutionError) as excinfo:
            provider.investigate(case, tools, InvestigationBudget(), {})
        assert excinfo.value.code == "FINAL_WITHOUT_CASE_EVIDENCE"
        # The dispatch happened and succeeded; it simply was not evidence.
        assert excinfo.value.tool_calls_used == 1
        assert excinfo.value.evidence_tool_calls == 0

    def test_a_wrong_type_spoof_then_final_also_fails(
        self, collision: tuple[CaseRecord, ToolDispatcher, CaseEvidenceIndex]
    ) -> None:
        case, tools, _index = collision
        provider = LLMInvestigatorProvider(
            self._chain(
                [
                    self._tool("get_record", record_id=f"REFUND:{SHARED_ID}"),
                    ZERO_TOOL_FINAL,
                    ZERO_TOOL_FINAL,
                ]
            )
        )
        with pytest.raises(InvestigatorExecutionError) as excinfo:
            provider.investigate(case, tools, InvestigationBudget(), {})
        assert excinfo.value.code == "FINAL_WITHOUT_CASE_EVIDENCE"
        assert excinfo.value.tool_calls_used == 1
        assert excinfo.value.evidence_tool_calls == 0

    def test_a_genuine_typed_record_then_final_is_accepted(
        self, collision: tuple[CaseRecord, ToolDispatcher, CaseEvidenceIndex]
    ) -> None:
        case, tools, _index = collision
        provider = LLMInvestigatorProvider(
            self._chain(
                [
                    self._tool("get_record", record_id=f"PAYMENT:{SHARED_ID}"),
                    ZERO_TOOL_FINAL,
                ]
            )
        )
        result = provider.investigate(case, tools, InvestigationBudget(), {})
        assert result.unresolved is not None
        assert result.evidence_tool_calls == 1

    def test_the_spoof_trace_records_relevance_and_no_record_prose(
        self, bench: tuple[CaseRecord, ToolDispatcher, str]
    ) -> None:
        case, tools, cited = bench
        provider = LLMInvestigatorProvider(
            self._chain(
                [
                    self._tool("check_unique_identity", record_ids=[case.case_id]),
                    self._tool("get_record", record_id=cited),
                    ZERO_TOOL_FINAL,
                ]
            )
        )
        result = provider.investigate(case, tools, InvestigationBudget(), {})
        steps = [step for step in result.trace if step.get("type") == "tool"]
        assert [step["tool"] for step in steps] == ["check_unique_identity", "get_record"]
        assert [step["case_evidence"] for step in steps] == [False, True]
        payload = json.dumps(result.trace, default=str)
        assert case.summary not in payload
        for step in steps:
            assert set(step) == {
                "step",
                "type",
                "tool",
                "outcome",
                "result_keys",
                "identifiers",
                "case_evidence",
            }
