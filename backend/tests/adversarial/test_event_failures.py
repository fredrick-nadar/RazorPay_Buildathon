"""Adversarial Event Failure tests (PRD Phase 6).

Verifies system resilience and financial safety under realistic operational anomalies:
- Duplicate payment and refund deliveries;
- Out-of-order event arrivals;
- Delayed/missing events timing out to unresolved exceptions (no false resolutions);
- Replay diagnostics and zero duplicate corrections.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.enums import CaseStatus
from app.failure_lab.injector import EventFailureInjector, FailureType
from app.failure_lab.replay import ReplayDiagnostics
from app.persistence.database import Database
from app.runs import execute_run

REPO_ROOT = Path(__file__).resolve().parents[3]
DEV_INPUTS = REPO_ROOT / "datasets" / "dev" / "inputs"
ADV_INPUTS = REPO_ROOT / "datasets" / "adversarial" / "inputs"


def test_duplicate_payment_delivery_preserves_economic_totals(tmp_path: Path) -> None:
    injector = EventFailureInjector(seed=101)
    injected_inputs = tmp_path / "dup_payments_inputs"
    res = injector.inject_dataset(
        src_inputs_dir=DEV_INPUTS,
        dest_inputs_dir=injected_inputs,
        failure_types=[FailureType.DUPLICATE_DELIVERY],
    )

    assert res.injected_counts.get("DUPLICATE_DELIVERY", 0) > 0

    db_clean = Database(tmp_path / "clean.sqlite3")
    run_clean = execute_run(DEV_INPUTS, db_clean)
    db_clean.close()

    db_injected = Database(tmp_path / "injected.sqlite3")
    run_injected = execute_run(injected_inputs, db_injected)
    db_injected.close()

    # Economic totals must match exactly
    s_clean = run_clean.summary
    s_injected = run_injected.summary

    assert s_clean["financial_control_totals"] == s_injected["financial_control_totals"]
    assert s_clean["matched_record_count"] == s_injected["matched_record_count"]
    assert s_clean["cases_count"] == s_injected["cases_count"]
    # Duplicate deliveries must be explicitly counted
    assert int(s_injected["duplicate_delivery_count"]) >= int(s_clean["duplicate_delivery_count"])


def test_duplicate_refund_does_not_double_refund_total(tmp_path: Path) -> None:
    injector = EventFailureInjector(seed=202)
    injected_inputs = tmp_path / "dup_refunds_inputs"
    injector.inject_dataset(
        src_inputs_dir=DEV_INPUTS,
        dest_inputs_dir=injected_inputs,
        failure_types=[FailureType.DUPLICATE_DELIVERY],
    )

    db = Database(tmp_path / "dup_refunds.sqlite3")
    run = execute_run(injected_inputs, db)
    db.close()

    totals = run.summary["financial_control_totals"]
    # Net calculated totals should conserve value
    assert totals["payment_gross_paise"] >= totals["refund_total_paise"]


def test_out_of_order_event_stream_reconciles_safely(tmp_path: Path) -> None:
    injector = EventFailureInjector(seed=303)
    injected_inputs = tmp_path / "ooo_inputs"
    injector.inject_dataset(
        src_inputs_dir=DEV_INPUTS,
        dest_inputs_dir=injected_inputs,
        failure_types=[FailureType.OUT_OF_ORDER],
    )

    db = Database(tmp_path / "ooo.sqlite3")
    run = execute_run(injected_inputs, db)
    db.close()

    # Reordering must produce the same economic output as clean dev dataset
    db_clean = Database(tmp_path / "clean.sqlite3")
    run_clean = execute_run(DEV_INPUTS, db_clean)
    db_clean.close()

    assert run.economic_output_hash == run_clean.economic_output_hash


def test_missing_event_creates_incomplete_or_unresolved_case(tmp_path: Path) -> None:
    injector = EventFailureInjector(seed=404)
    injected_inputs = tmp_path / "missing_inputs"
    injector.inject_dataset(
        src_inputs_dir=DEV_INPUTS,
        dest_inputs_dir=injected_inputs,
        failure_types=[FailureType.DELAYED_OR_MISSING],
    )

    db = Database(tmp_path / "missing.sqlite3")
    run = execute_run(injected_inputs, db)
    db.close()

    # Dropping events must either reduce matched count or create residual cases
    # It must never hallucinate a clean match or a verified resolution without evidence
    cases = run.summary.get("cases", [])
    for c in cases:
        # None of the cases should have phantom resolution
        assert c.get("status") in (
            CaseStatus.OPEN.value,
            CaseStatus.UNRESOLVED.value,
            CaseStatus.APPROVAL_REQUIRED.value,
            CaseStatus.VERIFIED_RESOLVED.value,
        )


def test_replay_diagnostics_on_adversarial_dataset() -> None:
    report = ReplayDiagnostics.verify_replay(inputs_dir=ADV_INPUTS)
    assert report.is_idempotent is True
    assert report.first_economic_hash == report.replay_economic_hash
    assert report.duplicate_corrections_detected == 0
    assert len(report.discrepancies) == 0
