"""Unit tests for the bounded AI investigator engine and FakeProvider (PRD 10, Phase 4)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.domain.enums import CaseStatus, ExceptionCategory, VerifierStatus
from app.domain.records import AcceptedRecords
from app.investigator.budgets import InvestigationBudget
from app.investigator.engine import investigate_cases
from app.investigator.provider import FakeProvider, InvestigatorProvider
from app.investigator.schemas import (
    ProviderResult,
)
from app.investigator.tools import ToolDispatcher
from app.persistence.database import Database
from app.reconciliation.detectors import CaseEvidence, CaseRecord
from app.runs import compute_idempotency_key, execute_run
from app.verifier.models import hypothesis_id_for
from tests.unit.recon_fixtures import ledger_row, payment, refund, settlement


def _make_duplicate_ledger_fixtures() -> tuple[AcceptedRecords, list[CaseRecord]]:
    p1 = payment("pay-001", gross=100000, fee=0, tax=0)
    l1 = ledger_row(
        "led-001",
        amount=100000,
        source_type="PAYMENT",
        source_reference="pay-001",
        account="2100-PAYMENTS-CLEARING",
    )
    l2 = ledger_row(
        "led-002",
        amount=100000,
        source_type="PAYMENT",
        source_reference="pay-001",
        account="2100-PAYMENTS-CLEARING",
    )
    records = AcceptedRecords(
        payments=(p1,),
        refunds=(),
        settlements=(),
        bank_entries=(),
        ledger_entries=(l1, l2),
    )
    case = CaseRecord(
        case_id="case-dup-01",
        category=ExceptionCategory.DUPLICATE_LEDGER_POSTING,
        status=CaseStatus.UNRESOLVED,
        variance_paise=100000,
        affected_amount_paise=100000,
        proposed_delta_paise=None,
        currency="INR",
        summary="duplicate ledger posting",
        reason_codes=("DUPLICATE_POSTING",),
        evidence=(
            CaseEvidence("PAYMENT", "pay-001"),
            CaseEvidence("LEDGER_ENTRY", "led-001"),
            CaseEvidence("LEDGER_ENTRY", "led-002"),
        ),
    )
    return records, [case]


def _make_missing_refund_fixtures() -> tuple[AcceptedRecords, list[CaseRecord]]:
    p1 = payment("pay-002", gross=100000, fee=0, tax=0, settlement_id="stl_S000000002")
    r1 = refund(
        "ref-002",
        payment_id="pay-002",
        amount=50000,
        created="2026-03-02T10:00:00Z",
        settlement_id="stl_S000000002",
    )
    s1 = settlement(
        "stl_S000000002",
        gross=100000,
        net=50000,
        window=("2026-03-02T00:00:00Z", "2026-03-02T23:59:59Z"),
    )
    records = AcceptedRecords(
        payments=(p1,),
        refunds=(r1,),
        settlements=(s1,),
        bank_entries=(),
        ledger_entries=(),
    )
    case = CaseRecord(
        case_id="case-ref-01",
        category=ExceptionCategory.MISSING_REFUND_POSTING,
        status=CaseStatus.UNRESOLVED,
        variance_paise=50000,
        affected_amount_paise=50000,
        proposed_delta_paise=None,
        currency="INR",
        summary="missing refund posting",
        reason_codes=("MISSING_POSTING",),
        evidence=(
            CaseEvidence("REFUND", "ref-002"),
            CaseEvidence("PAYMENT", "pay-002"),
            CaseEvidence("SETTLEMENT", "stl_S000000002"),
        ),
    )
    return records, [case]


def _make_timing_window_fixtures() -> tuple[AcceptedRecords, list[CaseRecord]]:
    s1 = settlement(
        "stl_S000000003",
        gross=100000,
        net=97640,
        window=("2026-03-02T00:00:00Z", "2026-03-02T23:59:59Z"),
    )
    l1 = ledger_row(
        "led-003",
        amount=97640,
        accounting_date="2026-03-04",
        source_type="SETTLEMENT",
        source_reference="stl_S000000003",
        account="1100-BANK-OPERATING",
    )
    records = AcceptedRecords(
        payments=(),
        refunds=(),
        settlements=(s1,),
        bank_entries=(),
        ledger_entries=(l1,),
    )
    case = CaseRecord(
        case_id="case-timing-01",
        category=ExceptionCategory.SETTLEMENT_TIMING_WINDOW_SHIFT,
        status=CaseStatus.UNRESOLVED,
        variance_paise=0,
        affected_amount_paise=97640,
        proposed_delta_paise=None,
        currency="INR",
        summary="timing window shift",
        reason_codes=("WINDOW_SHIFT",),
        evidence=(
            CaseEvidence("SETTLEMENT", "stl_S000000003"),
            CaseEvidence("LEDGER_ENTRY", "led-003"),
        ),
    )
    return records, [case]


def _make_ambiguous_fixtures() -> tuple[AcceptedRecords, list[CaseRecord]]:
    s1 = settlement("stl_S000000004", gross=100000, net=97640)
    records = AcceptedRecords(
        payments=(),
        refunds=(),
        settlements=(s1,),
        bank_entries=(),
        ledger_entries=(),
    )
    case = CaseRecord(
        case_id="case-amb-01",
        category=ExceptionCategory.AMBIGUOUS_EVIDENCE,
        status=CaseStatus.UNRESOLVED,
        variance_paise=0,
        affected_amount_paise=97640,
        proposed_delta_paise=None,
        currency="INR",
        summary="ambiguous evidence",
        reason_codes=("AMBIGUOUS",),
        evidence=(CaseEvidence("SETTLEMENT", "stl_S000000004"),),
    )
    return records, [case]


def test_fake_provider_resolves_duplicate_ledger() -> None:
    records, cases = _make_duplicate_ledger_fixtures()
    outcome = investigate_cases(records, cases, FakeProvider())

    assert len(outcome.investigations) == 1
    inv = outcome.investigations[0]
    assert inv.status == "RESOLVED"
    assert inv.case.status == CaseStatus.APPROVAL_REQUIRED
    assert inv.verifier_result is not None
    assert inv.verifier_result.status == VerifierStatus.PASS
    assert inv.verifier_result.proposed_delta_paise == -100000
    assert inv.proof is not None
    assert inv.dry_run is not None
    assert inv.dry_run.variance_after_paise == 0


def test_fake_provider_resolves_missing_refund() -> None:
    records, cases = _make_missing_refund_fixtures()
    outcome = investigate_cases(records, cases, FakeProvider())

    assert len(outcome.investigations) == 1
    inv = outcome.investigations[0]
    assert inv.status == "RESOLVED"
    assert inv.case.status == CaseStatus.APPROVAL_REQUIRED
    assert inv.verifier_result is not None
    assert inv.verifier_result.status == VerifierStatus.PASS
    assert inv.verifier_result.proposed_delta_paise == -50000
    assert inv.proof is not None
    assert inv.dry_run is not None


def test_fake_provider_resolves_timing_window() -> None:
    records, cases = _make_timing_window_fixtures()
    outcome = investigate_cases(records, cases, FakeProvider())

    assert len(outcome.investigations) == 1
    inv = outcome.investigations[0]
    assert inv.status == "RESOLVED"
    assert inv.case.status == CaseStatus.VERIFIED_RESOLVED
    assert inv.verifier_result is not None
    assert inv.verifier_result.status == VerifierStatus.PASS
    assert inv.verifier_result.proposed_delta_paise == 0
    assert inv.proof is not None
    assert inv.dry_run is not None


def test_fake_provider_escalates_ambiguous() -> None:
    records, cases = _make_ambiguous_fixtures()
    outcome = investigate_cases(records, cases, FakeProvider())

    assert len(outcome.investigations) == 1
    inv = outcome.investigations[0]
    assert inv.status == "UNRESOLVED"
    assert inv.case.status == CaseStatus.UNRESOLVED
    assert inv.provider_result is not None
    assert inv.provider_result.unresolved is not None
    assert inv.provider_result.unresolved.reason_codes == ("NON_UNIQUE_EVIDENCE",)


class TimeoutFakeProvider(InvestigatorProvider):
    @property
    def provider_id(self) -> str:
        return "timeout-fake-v1"

    def investigate(
        self,
        case: CaseRecord,
        tools: ToolDispatcher,
        budget: InvestigationBudget,
        context: dict[str, Any],
    ) -> ProviderResult:
        time.sleep(budget.timeout_s + 0.1)
        return FakeProvider().investigate(case, tools, budget, context)


def test_investigation_failed_on_timeout() -> None:
    records, cases = _make_duplicate_ledger_fixtures()
    budget = InvestigationBudget(timeout_s=0.1)
    outcome = investigate_cases(records, cases, TimeoutFakeProvider(), budget_config=budget)

    assert len(outcome.investigations) == 1
    inv = outcome.investigations[0]
    assert inv.status == "FAILED"
    assert inv.case.status == CaseStatus.INVESTIGATION_FAILED
    assert inv.failure_reason is not None
    assert "timeout" in inv.failure_reason


class ErrorFakeProvider(InvestigatorProvider):
    @property
    def provider_id(self) -> str:
        return "error-fake-v1"

    def investigate(
        self,
        case: CaseRecord,
        tools: ToolDispatcher,
        budget: InvestigationBudget,
        context: dict[str, Any],
    ) -> ProviderResult:
        raise RuntimeError("Simulated provider crash")


def test_investigation_failed_on_provider_error() -> None:
    records, cases = _make_duplicate_ledger_fixtures()
    outcome = investigate_cases(records, cases, ErrorFakeProvider())

    assert len(outcome.investigations) == 1
    inv = outcome.investigations[0]
    assert inv.status == "FAILED"
    assert inv.case.status == CaseStatus.INVESTIGATION_FAILED
    assert inv.failure_reason is not None
    assert "Simulated provider crash" in inv.failure_reason


def test_skips_non_investigable_cases() -> None:
    records, cases = _make_duplicate_ledger_fixtures()
    # Case is already APPROVAL_REQUIRED (PASS)
    cases[0] = CaseRecord(
        case_id=cases[0].case_id,
        category=cases[0].category,
        status=CaseStatus.APPROVAL_REQUIRED,
        variance_paise=cases[0].variance_paise,
        affected_amount_paise=cases[0].affected_amount_paise,
        proposed_delta_paise=100000,
        currency=cases[0].currency,
        summary=cases[0].summary,
        reason_codes=cases[0].reason_codes,
        evidence=cases[0].evidence,
    )
    outcome = investigate_cases(records, cases, FakeProvider())
    assert len(outcome.investigations) == 1
    inv = outcome.investigations[0]
    assert inv.status == "SKIPPED"
    assert inv.case.status == CaseStatus.APPROVAL_REQUIRED


def test_hypothesis_id_uses_phase3_function() -> None:
    records, cases = _make_duplicate_ledger_fixtures()
    outcome = investigate_cases(records, cases, FakeProvider())

    inv = outcome.investigations[0]
    assert inv.hypothesis is not None
    expected_id = hypothesis_id_for(
        cases[0].case_id,
        cases[0].category,
        inv.hypothesis.evidence_ids,
    )
    assert inv.hypothesis.hypothesis_id == expected_id


def test_investigation_summary_structure() -> None:
    records, cases = _make_duplicate_ledger_fixtures()
    outcome = investigate_cases(records, cases, FakeProvider())
    summary = outcome.summary()

    assert summary["provider_id"] == "fake-deterministic-v1"
    assert summary["status_counts"] == {"RESOLVED": 1}
    assert summary["total_tool_calls"] > 0
    assert len(summary["cases"]) == 1
    case_summary = summary["cases"][0]
    assert case_summary["case_id"] == cases[0].case_id
    assert case_summary["outcome"] == "RESOLVED"
    assert "proof_id" in case_summary


def test_agent_mode_idempotency_key_differs_from_rules_only() -> None:
    fingerprint = "abc123inputs"
    rules_key = compute_idempotency_key(fingerprint, mode="rules-only", provider_id="none")
    agent_key = compute_idempotency_key(
        fingerprint, mode="agent", provider_id="fake-deterministic-v1"
    )

    assert rules_key != agent_key
    assert len(rules_key) == 64
    assert len(agent_key) == 64


def test_agent_run_reused_false_after_rules_run(tmp_path: Path) -> None:
    dev_inputs = Path("datasets/dev/inputs")
    db1_path = tmp_path / "test_rules.sqlite3"
    db1 = Database(db1_path)
    db2_path = tmp_path / "test_agent.sqlite3"
    db2 = Database(db2_path)
    try:
        # Run 1: rules-only on db1
        res1 = execute_run(dev_inputs, db1, mode="rules-only")
        assert res1.reused is False
        assert res1.summary["mode"] == "rules-only"

        # Run 2: rules-only repeat on db1 -> reused is True
        res2 = execute_run(dev_inputs, db1, mode="rules-only")
        assert res2.reused is True

        # Run 3: agent mode on db2 -> fresh run, reused is False
        res3 = execute_run(dev_inputs, db2, mode="agent")
        assert res3.reused is False
        assert res3.summary["mode"] == "agent"
        assert "investigation" in res3.summary

        # Run 4: agent mode repeat on db2 -> reused is True
        res4 = execute_run(dev_inputs, db2, mode="agent")
        assert res4.reused is True

        # Assert distinct idempotency keys and run IDs
        assert res1.idempotency_key != res3.idempotency_key
        assert res1.run_id != res3.run_id
    finally:
        db1.close()
        db2.close()
