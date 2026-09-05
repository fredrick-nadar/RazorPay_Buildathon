"""Unit tests for investigator tool dispatcher and allowlist (PRD 10.2)."""

from __future__ import annotations

from app.domain.enums import CaseStatus, ExceptionCategory
from app.domain.records import AcceptedRecords
from app.graph.evidence import build_evidence_graph
from app.investigator.tools import TOOL_ALLOWLIST, ToolDispatcher
from app.reconciliation.detectors import CaseEvidence, CaseRecord
from app.verifier.snapshot import build_evidence_snapshot
from tests.unit.recon_fixtures import bank_credit, ledger_row, payment, refund, settlement


def _make_sample_dispatcher() -> ToolDispatcher:
    p1 = payment("pay-001", gross=100000, fee=2360, tax=360)
    r1 = refund("ref-001", payment_id="pay-001", amount=50000)
    s1 = settlement(
        "stl_S000000001",
        gross=100000,
        net=97640,
        window=("2026-03-02T00:00:00Z", "2026-03-02T23:59:59Z"),
    )
    b1 = bank_credit("bnk-001", amount=97640)
    l1 = ledger_row(
        "led-001",
        amount=100000,
        source_type="PAYMENT",
        source_reference="pay-001",
        account="2100-PAYMENTS-CLEARING",
    )
    records = AcceptedRecords(
        payments=(p1,),
        refunds=(r1,),
        settlements=(s1,),
        bank_entries=(b1,),
        ledger_entries=(l1,),
    )
    snapshot = build_evidence_snapshot(records)
    case1 = CaseRecord(
        case_id="case-test-01",
        category=ExceptionCategory.DUPLICATE_LEDGER_POSTING,
        status=CaseStatus.OPEN,
        variance_paise=100000,
        affected_amount_paise=100000,
        proposed_delta_paise=None,
        currency="INR",
        summary="duplicate ledger test",
        reason_codes=("DUPLICATE_POSTING",),
        evidence=(
            CaseEvidence("PAYMENT", "pay-001"),
            CaseEvidence("LEDGER_ENTRY", "led-001"),
        ),
    )
    graph = build_evidence_graph(records, [], [case1])
    return ToolDispatcher(
        snapshot=snapshot,
        records=records,
        cases={case1.case_id: case1},
        graph_json=graph.to_json(),
    )


def test_tool_allowlist_contains_only_read_and_calculation_tools() -> None:
    expected = frozenset(
        {
            "get_case",
            "get_evidence_graph",
            "get_record",
            "get_records",
            "list_candidate_records",
            "get_rule_manifest",
            "calculate_control_totals",
            "calculate_expected_net",
            "check_date_window",
            "check_unique_identity",
        }
    )
    assert expected == TOOL_ALLOWLIST


def test_no_workflow_or_state_tools_in_allowlist() -> None:
    forbidden = {
        "verify_hypothesis",
        "preview_correction",
        "record_hypothesis",
        "propose_resolution",
        "mark_unresolved",
        "approve",
        "apply",
        "update_ledger",
        "mark_resolved",
        "execute_sql",
        "run_code",
    }
    for tool in forbidden:
        assert tool not in TOOL_ALLOWLIST


def test_unknown_tool_returns_error() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch("delete_database", {})
    assert result == {
        "error": "UNKNOWN_TOOL",
        "detail": "tool 'delete_database' is not in the allowlist",
    }


def test_get_case_returns_case_dict() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch("get_case", {"case_id": "case-test-01"})
    assert result["case_id"] == "case-test-01"
    assert result["category"] == "DUPLICATE_LEDGER_POSTING"
    assert len(result["evidence"]) == 2


def test_get_case_unknown_returns_error() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch("get_case", {"case_id": "case-unknown"})
    assert result == {"error": "UNKNOWN_CASE", "detail": "case 'case-unknown' not found"}


def test_get_case_invalid_arguments() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch("get_case", {"case_id": 123})
    assert result == {"error": "INVALID_ARGUMENTS", "detail": "case_id must be a string"}


def test_get_record_validates_evidence_id_format() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch("get_record", {"record_id": "malformed_id"})
    assert result["error"] == "UNKNOWN_EVIDENCE_ID"


def test_get_record_unknown_id() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch("get_record", {"record_id": "PAYMENT:pay-nonexistent"})
    assert result["error"] == "UNKNOWN_EVIDENCE_ID"


def test_get_record_with_valid_id() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch("get_record", {"record_id": "PAYMENT:pay-001"})
    assert result["payment_id"] == "pay-001"
    assert result["gross_amount_paise"] == 100000


def test_get_records_returns_each_requested_record_with_its_canonical_id() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch(
        "get_records", {"record_ids": ["PAYMENT:pay-001", "LEDGER_ENTRY:led-001"]}
    )

    assert result["count"] == 2
    assert [item["evidence_id"] for item in result["records"]] == [
        "PAYMENT:pay-001",
        "LEDGER_ENTRY:led-001",
    ]


def test_get_records_never_silently_drops_an_unknown_id() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch(
        "get_records", {"record_ids": ["PAYMENT:pay-001", "PAYMENT:missing"]}
    )

    assert result["error"] == "UNKNOWN_EVIDENCE_ID"


def test_list_candidate_records() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch("list_candidate_records", {"record_type": "PAYMENT"})
    assert result["record_type"] == "PAYMENT"
    assert result["count"] == 1
    assert "pay-001" in result["records"]


def test_list_candidate_records_invalid_type() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch("list_candidate_records", {"record_type": "INVALID_TYPE"})
    assert result["error"] == "INVALID_ARGUMENTS"


def test_get_evidence_graph() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch("get_evidence_graph", {})
    assert "nodes" in result
    assert "edges" in result


def test_get_rule_manifest() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch("get_rule_manifest", {})
    assert "reconciliation" in result
    assert "verification" in result


def test_calculate_control_totals_exploratory_note() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch("calculate_control_totals", {})
    assert "note" in result
    assert "exploratory" in result["note"]
    assert "totals" in result


def test_calculate_expected_net() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch(
        "calculate_expected_net",
        {"payment_ids": ["pay-001"], "refund_ids": ["ref-001"]},
    )
    assert result["note"] == "exploratory — verifier is authoritative"
    assert result["gross_paise"] == 100000
    assert result["fee_paise"] == 2360
    assert result["tax_paise"] == 360
    assert result["refund_total_paise"] == 50000
    assert result["expected_net_paise"] == 100000 - 2360 - 360 - 50000


def test_check_date_window() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch(
        "check_date_window",
        {"record_ids": ["SETTLEMENT:stl_S000000001", "LEDGER_ENTRY:led-001"]},
    )
    assert result["note"] == "exploratory — verifier is authoritative"
    assert len(result["records"]) == 2


def test_check_unique_identity() -> None:
    dispatcher = _make_sample_dispatcher()
    result = dispatcher.dispatch(
        "check_unique_identity",
        {"record_ids": ["PAYMENT:pay-001"]},
    )
    assert result["note"] == "exploratory — verifier is authoritative"
    assert result["is_unique"] is True
    assert result["unique_count"] == 1
