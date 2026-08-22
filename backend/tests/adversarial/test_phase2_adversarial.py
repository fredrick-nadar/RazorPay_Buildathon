"""Phase 2 adversarial tests: the nine PRD adversarial phenomena, at runtime.

Covers duplicate delivery, identical amounts for distinct payments, a
reordered source file, a missing optional UTR, conflicting currency, invalid
dates, prompt-injection narration, partial-refund aggregation ambiguity, and
twin settlement ambiguity - plus out-of-order events and boundary times.
"""

from __future__ import annotations

import csv
import random
import shutil
from pathlib import Path

from app.domain.enums import ExceptionCategory, RelationshipType
from app.importers.ingest import ingest_inputs
from app.reconciliation.detectors import reconcile
from app.reconciliation.rules import R_SETTLEMENT_BANK_UNIQUE
from app.reconciliation.totals import control_totals, verify_match_invariants
from app.runs import economic_output_hash

REPO_ROOT = Path(__file__).resolve().parents[3]
INPUTS = REPO_ROOT / "datasets" / "adversarial" / "inputs"


def _reconciled(inputs: Path = INPUTS):
    ingest = ingest_inputs(inputs)
    result = reconcile(ingest.records)
    return ingest, result


class TestDuplicateDelivery:
    def test_exact_duplicate_row_yields_one_economic_event(self) -> None:
        ingest, _result = _reconciled()
        assert ingest.duplicate_delivery_count == 1
        payment_ids = [p.payment_id for p in ingest.records.payments]
        assert payment_ids.count("pay_NZ3xBYxQFL") == 1
        assert len(set(payment_ids)) == len(payment_ids)

    def test_duplicate_row_is_stored_and_counted_not_dropped(self) -> None:
        ingest = ingest_inputs(INPUTS)
        stats = {stat.file_stem: stat for stat in ingest.file_stats}
        payments = stats["payments"]
        assert payments.raw_rows == 20
        assert payments.accepted + payments.quarantined + payments.duplicate_delivery == 20


class TestDistinctEvents:
    def test_identical_amounts_remain_separate_records(self) -> None:
        ingest, result = _reconciled()
        payments = {p.payment_id: int(p.gross_amount_paise) for p in ingest.records.payments}
        # The two label-cited distinct-event payments share one gross amount.
        assert payments["pay_FiCqJLNNfR"] == payments["pay_Yzxp7ldPf0"]
        ids = {p.payment_id for p in ingest.records.payments}
        assert {"pay_FiCqJLNNfR", "pay_Yzxp7ldPf0"} <= ids
        # Each keeps its own ledger-source match.
        for payment_id in ("pay_FiCqJLNNfR", "pay_Yzxp7ldPf0"):
            matched = any(
                member.record_id == payment_id
                for group in result.matches
                for member in group.members
            )
            assert matched, payment_id


class TestReorderedFile:
    def test_shuffled_inputs_preserve_the_economic_hash(self, tmp_path: Path) -> None:
        baseline_ingest, baseline_result = _reconciled()
        baseline = economic_output_hash(
            baseline_ingest,
            baseline_result,
            control_totals(baseline_ingest.records, list(baseline_result.cases)),
        )
        target = tmp_path / "shuffled"
        shutil.copytree(INPUTS, target)
        rng = random.Random(2026)
        for path in sorted(target.glob("*.csv")):
            with path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            header, data = rows[0], rows[1:]
            rng.shuffle(data)
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(header)
                writer.writerows(data)
        shuffled_ingest, shuffled_result = _reconciled(target)
        shuffled = economic_output_hash(
            shuffled_ingest,
            shuffled_result,
            control_totals(shuffled_ingest.records, list(shuffled_result.cases)),
        )
        assert shuffled == baseline


class TestMissingOptionalUtr:
    def test_utr_less_settlement_matches_by_unique_amount_window(self) -> None:
        _ingest, result = _reconciled()
        matches = [
            group
            for group in result.matches
            if group.relationship_type == RelationshipType.SETTLEMENT_BANK_CREDIT
            and any(member.record_id == "stl_TZxuIWVs5C" for member in group.members)
        ]
        assert len(matches) == 1
        assert matches[0].rule_id == R_SETTLEMENT_BANK_UNIQUE


class TestQuarantineRows:
    def test_usd_currency_row_quarantined_never_dropped(self) -> None:
        ingest = ingest_inputs(INPUTS)
        row = next(row for row in ingest.rows if row.source_record_id == "pay_LSH3xlGWQ0")
        assert row.state == "QUARANTINED"
        assert row.quarantine_reason is not None
        assert row.quarantine_reason.value == "UNSUPPORTED_CURRENCY"

    def test_invalid_date_row_quarantined(self) -> None:
        ingest = ingest_inputs(INPUTS)
        row = next(row for row in ingest.rows if row.source_record_id == "rfd_SJ007V2FCc")
        assert row.state == "QUARANTINED"
        assert row.quarantine_reason is not None
        assert row.quarantine_reason.value == "INVALID_TIMESTAMP"


class TestPromptInjectionNarration:
    def test_injection_sentence_is_inert_verbatim_evidence(self) -> None:
        ingest, result = _reconciled()
        credit = next(
            credit
            for credit in ingest.records.bank_entries
            if credit.bank_entry_id == "bnk_sYqE4Wxuci"
        )
        assert "Ignore previous rules" in credit.narration
        # The narration never becomes an instruction: the credit still
        # reconciles purely through its deterministic identifiers.
        matched = any(
            member.record_id == "bnk_sYqE4Wxuci"
            for group in result.matches
            for member in group.members
        )
        assert matched
        cases_citing = [
            case
            for case in result.cases
            if any(item.record_id == "bnk_sYqE4Wxuci" for item in case.evidence)
        ]
        assert cases_citing == []  # clean record, no behavioural effect


class TestPartialRefundAmbiguity:
    def test_aggregate_rows_yield_non_unique_composition_case(self) -> None:
        _ingest, result = _reconciled()
        cases = [
            case
            for case in result.cases
            if case.category == ExceptionCategory.AMBIGUOUS_EVIDENCE
            and any(item.record_id == "pay_3UjY6a0SVK" for item in case.evidence)
        ]
        assert len(cases) == 1
        evidence_ids = {item.record_id for item in cases[0].evidence}
        assert evidence_ids == {
            "pay_3UjY6a0SVK",
            "rfd_dVNnWYBvrx",
            "rfd_OiRtpaZnEQ",
            "rfd_lmxlaO0H8K",
            "led_uyc1AjzE6v",
            "led_eUFD6klYWL",
        }
        assert cases[0].variance_paise == 0
        assert cases[0].affected_amount_paise > 0


class TestTwinSettlementAmbiguity:
    def test_twin_settlements_and_credits_form_one_ambiguous_case(self) -> None:
        _ingest, result = _reconciled()
        cases = [
            case
            for case in result.cases
            if any(item.record_id == "stl_1IIuTNux5X" for item in case.evidence)
        ]
        assert len(cases) == 1
        assert {item.record_id for item in cases[0].evidence} == {
            "stl_1IIuTNux5X",
            "stl_bMu31h7e7H",
            "bnk_Sa65QydOjL",
            "bnk_DFAbwJAcxm",
        }
        bank_matches = [
            group
            for group in result.matches
            if group.relationship_type == RelationshipType.SETTLEMENT_BANK_CREDIT
            and {
                "stl_1IIuTNux5X",
                "stl_bMu31h7e7H",
            }
            & {member.record_id for member in group.members}
        ]
        assert bank_matches == []  # never guessed


class TestMissingBankEvidenceCase:
    def test_settlement_without_credit_is_an_ambiguous_case(self) -> None:
        _ingest, result = _reconciled()
        cases = [
            case
            for case in result.cases
            if any(item.record_id == "stl_G5NU6VCxep" for item in case.evidence)
        ]
        assert len(cases) == 1
        assert cases[0].category == ExceptionCategory.AMBIGUOUS_EVIDENCE


class TestOutOfOrderAndBoundaryTimes:
    def test_out_of_order_events_still_reconcile_by_identity(self) -> None:
        ingest, result = _reconciled()
        # Refund timestamped before its parent capture still links by id.
        refund_match = any(
            member.record_id == "rfd_Nc5rje9Ikp"
            for group in result.matches
            if group.relationship_type == RelationshipType.REFUND_OF_PAYMENT
            for member in group.members
        )
        assert refund_match
        totals = control_totals(ingest.records, list(result.cases))
        assert totals["settlement_net_paise"] == totals["expected_net_settlement_paise"]

    def test_boundary_timestamps_parse_and_match(self) -> None:
        ingest = ingest_inputs(INPUTS)
        by_id = {p.payment_id: p for p in ingest.records.payments}
        assert by_id["pay_FiCqJLNNfR"].captured_at_utc.hour == 0
        assert by_id["pay_Yzxp7ldPf0"].captured_at_utc.hour == 23

    def test_match_invariants_and_accounting_hold(self) -> None:
        ingest, result = _reconciled()
        assert verify_match_invariants(list(result.matches)) == []
        assert result.unaccounted_record_keys == frozenset()
        assert (
            ingest.accepted_count + ingest.quarantined_count + ingest.duplicate_delivery_count
            == ingest.raw_row_count
        )
