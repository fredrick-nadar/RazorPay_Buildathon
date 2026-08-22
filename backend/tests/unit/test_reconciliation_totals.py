"""Phase 2 control-total tests: conservation, invariants, residual variance.

Totals must be reproducible from stored normalized records, match the
evaluator-side labels manifest totals, satisfy the clean conservation
identities, and reconcile with the labelled clean reference.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.importers.ingest import ingest_inputs
from app.reconciliation.detectors import reconcile
from app.reconciliation.totals import control_totals, verify_match_invariants

REPO_ROOT = Path(__file__).resolve().parents[3]


def _run(inputs: Path) -> tuple:
    ingest = ingest_inputs(inputs)
    result = reconcile(ingest.records)
    totals = control_totals(ingest.records, list(result.cases))
    return ingest, result, totals


def _labels_manifest(profile: str) -> dict:
    return json.loads(
        (REPO_ROOT / "datasets" / profile / "labels" / "manifest.json").read_text(encoding="utf-8")
    )


def _labels(profile: str) -> dict:
    return json.loads(
        (REPO_ROOT / "datasets" / profile / "labels" / "labels.json").read_text(encoding="utf-8")
    )


class TestDevControlTotals:
    def test_totals_match_labels_manifest(self) -> None:
        _ingest, _result, totals = _run(REPO_ROOT / "datasets" / "dev" / "inputs")
        expected = _labels_manifest("dev")["totals_paise"]
        assert totals["payment_gross_paise"] == expected["payment_gross"]
        assert totals["payment_fee_paise"] == expected["payment_fee"]
        assert totals["payment_tax_paise"] == expected["payment_tax"]
        assert totals["payment_net_paise"] == expected["payment_net"]
        assert totals["refund_total_paise"] == expected["refund_total"]
        assert totals["settlement_net_paise"] == expected["settlement_net"]
        assert totals["bank_credit_paise"] == expected["bank_credit"]
        assert totals["ledger_total_paise"] == expected["ledger_total"]
        assert totals["ledger_by_account_paise"] == expected["ledger_by_account"]

    def test_conservation_identities(self) -> None:
        _ingest, _result, totals = _run(REPO_ROOT / "datasets" / "dev" / "inputs")
        assert totals["settlement_net_paise"] == totals["expected_net_settlement_paise"]
        assert totals["bank_credit_paise"] + 0 == totals["settlement_net_paise"]

    def test_ledger_scoped_residual_equals_clean_reference_gap(self) -> None:
        _ingest, result, totals = _run(REPO_ROOT / "datasets" / "dev" / "inputs")
        clean_total = _labels("dev")["clean_reference"]["ledger_total_paise"]
        evaluator_gap = abs(totals["ledger_total_paise"] - clean_total)
        runtime_ledger_scope = sum(
            abs(case.variance_paise) for case in result.cases if case.variance_scope == "LEDGER"
        )
        assert runtime_ledger_scope == evaluator_gap

    def test_match_invariants_hold(self) -> None:
        _ingest, result, _totals = _run(REPO_ROOT / "datasets" / "dev" / "inputs")
        assert verify_match_invariants(list(result.matches)) == []


class TestAdversarialControlTotals:
    def test_totals_match_labels_manifest(self) -> None:
        _ingest, _result, totals = _run(REPO_ROOT / "datasets" / "adversarial" / "inputs")
        expected = _labels_manifest("adversarial")["totals_paise"]
        assert totals["payment_net_paise"] == expected["payment_net"]
        assert totals["settlement_net_paise"] == expected["settlement_net"]
        assert totals["bank_credit_paise"] == expected["bank_credit"]
        assert totals["ledger_by_account_paise"] == expected["ledger_by_account"]

    def test_match_invariants_hold(self) -> None:
        _ingest, result, _totals = _run(REPO_ROOT / "datasets" / "adversarial" / "inputs")
        assert verify_match_invariants(list(result.matches)) == []

    def test_residual_split_by_scope(self) -> None:
        _ingest, result, totals = _run(REPO_ROOT / "datasets" / "adversarial" / "inputs")
        clean_total = _labels("adversarial")["clean_reference"]["ledger_total_paise"]
        evaluator_gap = abs(totals["ledger_total_paise"] - clean_total)
        runtime_ledger_scope = sum(
            abs(case.variance_paise) for case in result.cases if case.variance_scope == "LEDGER"
        )
        runtime_bank_scope = sum(
            abs(case.variance_paise) for case in result.cases if case.variance_scope == "BANK"
        )
        assert evaluator_gap == 0  # adversarial anomalies never touch the ledger sum
        assert runtime_ledger_scope == 0
        assert runtime_bank_scope > 0  # the missing-bank residual


class TestInvariantNegatives:
    def test_broken_contribution_fails_the_invariant_check(self) -> None:
        from app.domain.enums import RelationshipType
        from app.reconciliation.engine import MatchGroup, MatchMember
        from app.reconciliation.rules import R_REFUND_TO_PAYMENT

        group = MatchGroup(
            match_id="match-broken",
            relationship_type=RelationshipType.REFUND_OF_PAYMENT,
            rule_id=R_REFUND_TO_PAYMENT,
            rule_version="1",
            amount_paise=100,
            members=(
                MatchMember("REFUND", "rfd_X", "CHILD", -100),
                MatchMember("PAYMENT", "pay_X", "PARENT", 90),  # not ±100
            ),
        )
        problems = verify_match_invariants([group])
        assert problems != []
