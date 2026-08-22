"""Phase 1 dataset tests: determinism, conservation, integrity, labels.

Everything here is evaluator-side: these tests read the committed datasets
and freshly generated temp datasets through ``app.evaluation`` only. The
independent checks in ``control_totals`` re-derive every fact from written
files, satisfying the PRD requirement that the generator never also be the
sole judge of its own output.
"""

from __future__ import annotations

import dataclasses
import re
import time
from pathlib import Path

from app.evaluation import control_totals as ct
from app.evaluation import dataset_io
from app.evaluation.dataset_spec import (
    ADVERSARIAL_SPEC,
    BENCHMARK_SPEC,
    DEV_SPEC,
    INPUT_FILES,
)
from app.evaluation.generator import generate_dataset

REPO_ROOT = Path(__file__).resolve().parents[3]
DATASETS_ROOT = REPO_ROOT / "datasets"

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
AMOUNT_RE = re.compile(r"^-?\d+\.\d{2}$")

ALL_CATEGORIES = {
    "DUPLICATE_LEDGER_POSTING",
    "MISSING_REFUND_POSTING",
    "SETTLEMENT_TIMING_WINDOW_SHIFT",
    "AMBIGUOUS_EVIDENCE",
}


def generate_to(root: Path, spec) -> Path:
    result = generate_dataset(spec)
    profile_dir = root / spec.profile
    dataset_io.write_dataset(profile_dir, result)
    return profile_dir


def file_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def committed(profile: str) -> Path:
    return DATASETS_ROOT / profile


class TestDeterminism:
    def test_same_seed_produces_byte_identical_tree(self, tmp_path: Path) -> None:
        first = generate_to(tmp_path / "a", DEV_SPEC)
        second = generate_to(tmp_path / "b", DEV_SPEC)
        assert file_tree(first) == file_tree(second)

    def test_labels_manifest_is_byte_identical_too(self, tmp_path: Path) -> None:
        first = generate_to(tmp_path / "a", DEV_SPEC)
        second = generate_to(tmp_path / "b", DEV_SPEC)
        for relative in ("labels/labels.json", "labels/manifest.json"):
            assert (first / relative).read_bytes() == (second / relative).read_bytes()

    def test_different_seed_changes_identities_and_labels(self) -> None:
        base = generate_dataset(DEV_SPEC)
        other = generate_dataset(dataclasses.replace(DEV_SPEC, seed=4242))
        base_ids = {row["payment_id"] for row in base.rows["payments"]}
        other_ids = {row["payment_id"] for row in other.rows["payments"]}
        assert base_ids.isdisjoint(other_ids)
        assert base.labels != other.labels

    def test_committed_dev_matches_regeneration(self, tmp_path: Path) -> None:
        fresh = generate_to(tmp_path, DEV_SPEC)
        assert file_tree(fresh) == file_tree(committed("dev"))

    def test_committed_adversarial_matches_regeneration(self, tmp_path: Path) -> None:
        fresh = generate_to(tmp_path, ADVERSARIAL_SPEC)
        assert file_tree(fresh) == file_tree(committed("adversarial"))


class TestSchemas:
    def test_timestamps_and_amounts_use_documented_formats(self) -> None:
        for profile in ("dev", "adversarial"):
            ds = ct.parse_dataset(committed(profile))
            quarantined = ct.quarantine_keys(ds.labels)
            amount_columns = {
                "payments": ("gross_amount", "fee_amount", "tax_amount"),
                "refunds": ("refund_amount",),
                "settlements": (
                    "gross_credit",
                    "fee_amount",
                    "tax_amount",
                    "adjustment_amount",
                    "net_amount",
                ),
                "bank_entries": ("signed_amount",),
                "ledger_entries": ("signed_amount",),
            }
            timestamp_columns = {
                "payments": ("captured_at_utc",),
                "refunds": ("created_at_utc",),
                "settlements": ("settled_at_utc", "window_start_utc", "window_end_utc"),
                "bank_entries": ("posted_at_utc",),
            }
            for name in INPUT_FILES:
                rows: tuple[dict[str, str], ...] = getattr(ds, name)
                for index, row in enumerate(rows, start=1):
                    if (name, index) in quarantined:
                        continue
                    for column in amount_columns[name]:
                        assert AMOUNT_RE.fullmatch(row[column]), (profile, name, column)
                    for column in timestamp_columns.get(name, ()):
                        assert TS_RE.fullmatch(row[column]), (profile, name, column)

    def test_currency_is_inr_except_the_labelled_quarantine_row(self) -> None:
        ds = ct.parse_dataset(committed("adversarial"))
        quarantined = ct.quarantine_keys(ds.labels)
        usd_rows = 0
        for index, row in enumerate(ds.payments, start=1):
            if row["currency"] == "USD":
                assert ("payments", index) in quarantined
                usd_rows += 1
            else:
                assert row["currency"] == "INR"
        assert usd_rows == 1
        for row in ct.parse_dataset(committed("dev")).payments:
            assert row["currency"] == "INR"

    def test_identifiers_unique_except_labelled_duplicate_delivery(self) -> None:
        for profile in ("dev", "adversarial"):
            ds = ct.parse_dataset(committed(profile))
            assert ct.referential_integrity_violations(ds) == []


class TestConservation:
    """Review correction 1: clean conservation vs post-injection variance."""

    def test_clean_identities_hold_on_eligible_rows(self) -> None:
        for profile in ("dev", "adversarial"):
            ds = ct.parse_dataset(committed(profile))
            assert ct.settlement_conservation_violations(ds) == []
            assert ct.corpus_identity_violations(ds) == []

    def test_variance_equation_holds_per_case_and_aggregate(self) -> None:
        for profile in ("dev", "adversarial"):
            ds = ct.parse_dataset(committed(profile))
            assert ct.variance_equation_violations(ds) == []

    def test_duplicate_and_missing_refund_cannot_cancel(self) -> None:
        ds = ct.parse_dataset(committed("dev"))
        labels = ds.labels
        assert labels is not None
        by_reference, _, _ = ct._ledger_sums(ds)
        payments = {row["payment_id"]: row for row in ds.payments}
        refunds = {row["refund_id"]: row for row in ds.refunds}
        duplicate_refs: set[str] = set()
        missing_refs: set[str] = set()
        for case in labels["cases"]:
            category = case["expected_category"]
            delta = case["expected_delta_paise"]
            if category == "DUPLICATE_LEDGER_POSTING":
                ref = next(e for e in case["expected_evidence_ids"] if e.startswith("pay_"))
                duplicate_refs.add(ref)
                expected = ct.payment_net(payments[ref])
                # two identical postings present: removing one restores balance
                assert by_reference[ref] == 2 * expected
                assert by_reference[ref] + delta == expected
            elif category == "MISSING_REFUND_POSTING":
                ref = next(e for e in case["expected_evidence_ids"] if e.startswith("rfd_"))
                missing_refs.add(ref)
                expected = -ct.amount_of(refunds[ref], "refund_amount")
                assert by_reference.get(ref, 0) == 0
                assert by_reference.get(ref, 0) + delta == expected
        assert duplicate_refs and missing_refs
        assert duplicate_refs.isdisjoint(missing_refs)

    def test_refunds_never_exceed_parent_payment(self) -> None:
        for profile in ("dev", "adversarial"):
            ds = ct.parse_dataset(committed(profile))
            view = ct.eligible_view(ds)
            gross = {
                row["payment_id"]: ct.amount_of(row, "gross_amount") for row in view.rows.payments
            }
            totals: dict[str, int] = {}
            for row in view.rows.refunds:
                totals[row["payment_id"]] = totals.get(row["payment_id"], 0) + ct.amount_of(
                    row, "refund_amount"
                )
            for payment_id, refunded in totals.items():
                assert refunded <= gross[payment_id], (profile, payment_id)


class TestReferentialIntegrity:
    """Review correction 2: explicit cross-file reference checks."""

    def test_no_violations_in_either_profile(self) -> None:
        for profile in ("dev", "adversarial"):
            ds = ct.parse_dataset(committed(profile))
            assert ct.referential_integrity_violations(ds) == []

    def test_every_eligible_refund_references_an_existing_payment(self) -> None:
        for profile in ("dev", "adversarial"):
            ds = ct.parse_dataset(committed(profile))
            view = ct.eligible_view(ds)
            payment_ids = {row["payment_id"] for row in view.rows.payments}
            for row in view.rows.refunds:
                assert row["payment_id"] in payment_ids

    def test_every_payment_settlement_reference_resolves(self) -> None:
        for profile in ("dev", "adversarial"):
            ds = ct.parse_dataset(committed(profile))
            view = ct.eligible_view(ds)
            settlement_ids = {row["settlement_id"] for row in view.rows.settlements}
            for row in view.rows.payments:
                if row["settlement_id"]:
                    assert row["settlement_id"] in settlement_ids

    def test_ledger_references_resolve_by_source_type(self) -> None:
        for profile in ("dev", "adversarial"):
            ds = ct.parse_dataset(committed(profile))
            by_file = {
                "PAYMENT": {row["payment_id"] for row in ds.payments},
                "REFUND": {row["refund_id"] for row in ds.refunds},
                "SETTLEMENT": {row["settlement_id"] for row in ds.settlements},
            }
            for row in ds.ledger_entries:
                assert row["source_reference"] in by_file[row["source_type"]]

    def test_utr_pairing_valid_when_present(self) -> None:
        # Covered by referential_integrity_violations (zero failures include
        # the UTR rules); spot-check the labelled missing-bank exception.
        ds = ct.parse_dataset(committed("adversarial"))
        view = ct.eligible_view(ds)
        settlement_map = ct.settlement_credit_candidates(ds)
        evidence = [
            [str(item) for item in case["expected_evidence_ids"]]
            for case in (ds.labels or {"cases": []})["cases"]
        ]
        labelled = {sid for ids in evidence for sid in ids}
        zero_credit = [sid for sid, credits in settlement_map.items() if not credits]
        assert zero_credit, "the missing-bank fixture must exist"
        for sid in zero_credit:
            assert sid in labelled
            assert sid in {row["settlement_id"] for row in view.rows.settlements}

    def test_quarantined_rows_are_exactly_the_labelled_ones(self) -> None:
        ds = ct.parse_dataset(committed("adversarial"))
        view = ct.eligible_view(ds)
        assert len(view.quarantine_rows) == 2
        expectations = {
            (str(e.get("file", "")).removesuffix(".csv"), e.get("row_number"))
            for e in (ds.labels or {"row_expectations": []})["row_expectations"]
            if str(e.get("expectation", "")).startswith("QUARANTINE")
        }
        actual = {(name, index) for name, index, _ in view.quarantine_rows}
        assert actual == expectations


class TestLabelsMetadata:
    """Review correction 3: evaluator-side label integrity metadata."""

    def test_labels_manifest_hashes_labels_json(self) -> None:
        for profile in ("dev", "adversarial"):
            assert ct.labels_manifest_violations(committed(profile)) == []

    def test_root_manifest_is_strictly_input_only(self) -> None:
        for profile in ("dev", "adversarial"):
            root = committed(profile)
            assert ct.root_manifest_violations(root) == []
            manifest_text = (root / "manifest.json").read_text(encoding="utf-8")
            for forbidden in (
                "eligible_row_count",
                "quarantine_expected_count",
                "duplicate_delivery_count",
                "totals_paise",
                "case_count",
                "clean_reference",
            ):
                assert forbidden not in manifest_text

    def test_labels_manifest_carries_anomaly_aware_metrics(self) -> None:
        import json

        for profile in ("dev", "adversarial"):
            manifest = json.loads(
                (committed(profile) / "labels" / "manifest.json").read_text(encoding="utf-8")
            )
            for key in (
                "eligible_row_count",
                "quarantine_expected_count",
                "duplicate_delivery_count",
                "totals_paise",
            ):
                assert key in manifest

    def test_root_manifest_has_no_wall_clock_keys(self) -> None:
        import json

        for profile in ("dev", "adversarial"):
            manifest = json.loads(
                (committed(profile) / "manifest.json").read_text(encoding="utf-8")
            )
            for key in manifest:
                assert "time" not in key.lower()
                assert "generated_at" not in key.lower()
                assert "created" not in key.lower()


class TestStructureAndCandidates:
    """Review correction 2: the four separated candidate-count rules."""

    def test_dev_has_at_least_100_eligible_records(self) -> None:
        ds = ct.parse_dataset(committed("dev"))
        metrics = ct.eligible_metrics(ds)
        assert metrics["eligible_row_count"] >= 100

    def test_all_four_categories_represented_in_dev(self) -> None:
        labels = ct.parse_dataset(committed("dev")).labels
        assert labels is not None
        by_category = labels["summary"]["by_category"]
        assert set(by_category) == ALL_CATEGORIES
        assert all(count == 3 for count in by_category.values())

    def test_label_categories_and_outcomes_use_frozen_enums(self) -> None:
        from app.domain.enums import ExceptionCategory

        valid_outcomes = {"APPROVAL_REQUIRED", "VERIFIED_RESOLVED", "UNRESOLVED"}
        for profile in ("dev", "adversarial"):
            labels = ct.parse_dataset(committed(profile)).labels
            assert labels is not None
            for case in labels["cases"]:
                assert case["expected_category"] in {c.value for c in ExceptionCategory}
                assert case["expected_outcome"] in valid_outcomes
                if case["expected_category"] == "AMBIGUOUS_EVIDENCE":
                    assert case["expected_delta_paise"] is None
                    assert case["must_escalate"] is True

    def test_clean_records_remain_clean(self) -> None:
        for profile in ("dev", "adversarial"):
            ds = ct.parse_dataset(committed(profile))
            assert ct.clean_structure_violations(ds) == []

    def test_candidate_rules_hold(self) -> None:
        for profile in ("dev", "adversarial"):
            ds = ct.parse_dataset(committed(profile))
            assert ct.candidate_count_violations(ds) == []

    def test_twin_credits_have_exactly_two_candidates(self) -> None:
        ds = ct.parse_dataset(committed("dev"))
        credit_map = ct.credit_settlement_candidates(ds)
        twin_credit_counts = sorted(
            len(candidates)
            for credit_id, candidates in credit_map.items()
            if any(
                credit_id in [str(i) for i in case["expected_evidence_ids"]]
                for case in (ds.labels or {"cases": []})["cases"]
            )
        )
        # 3 ambiguity pairs, 2 UTR-less credits each, every one with exactly
        # two valid settlement candidates.
        assert twin_credit_counts == [2] * 6

    def test_missing_bank_settlement_has_zero_candidates(self) -> None:
        ds = ct.parse_dataset(committed("adversarial"))
        settlement_map = ct.settlement_credit_candidates(ds)
        zero = [sid for sid, credits in settlement_map.items() if len(credits) == 0]
        assert len(zero) == 1
        assert (
            next(
                case
                for case in (ds.labels or {"cases": []})["cases"]
                if zero[0] in [str(i) for i in case["expected_evidence_ids"]]
            )["expected_category"]
            == "AMBIGUOUS_EVIDENCE"
        )

    def test_partial_refund_fixture_has_two_composition_candidates(self) -> None:
        ds = ct.parse_dataset(committed("adversarial"))
        labels = ds.labels
        assert labels is not None
        case = next(
            c
            for c in labels["cases"]
            if sum(1 for e in c["expected_evidence_ids"] if str(e).startswith("rfd_")) == 3
        )
        payment_id = next(
            str(e) for e in case["expected_evidence_ids"] if str(e).startswith("pay_")
        )
        aggregate_rows = [
            row
            for row in ds.ledger_entries
            if row["source_reference"] == payment_id
            and row["source_type"] == "PAYMENT"
            and ct.amount_of(row, "signed_amount") < 0
        ]
        assert len(aggregate_rows) == 2
        for row in aggregate_rows:
            amount = -ct.amount_of(row, "signed_amount")
            compositions = ct.refund_composition_candidates(ds, payment_id, amount)
            assert len(compositions) == 2

    def test_utrless_unique_settlement_has_exactly_one_candidate(self) -> None:
        ds = ct.parse_dataset(committed("adversarial"))
        settlement_map = ct.settlement_credit_candidates(ds)
        utrless = [
            row["settlement_id"]
            for row in ct.eligible_view(ds).rows.settlements
            if row["utr"] == ""
            # twin settlements are excluded: they are ambiguous, not unique
            and not any(
                row["settlement_id"] in [str(i) for i in case["expected_evidence_ids"]]
                for case in (ds.labels or {"cases": []})["cases"]
            )
        ]
        assert len(utrless) == 1
        assert len(settlement_map[utrless[0]]) == 1


class TestScale:
    def test_benchmark_sized_spec_generates_500_plus_rows(self, tmp_path: Path) -> None:
        started = time.perf_counter()
        result = generate_dataset(BENCHMARK_SPEC)
        elapsed = time.perf_counter() - started
        total_rows = sum(len(rows) for rows in result.rows.values())
        assert total_rows >= 500
        assert result.label_metrics["eligible_row_count"] >= 500
        assert elapsed < 30
        ds = ct.rows_to_dataset_rows(result.rows, result.labels)
        assert ct.settlement_conservation_violations(ds) == []
        assert ct.variance_equation_violations(ds) == []
        assert ct.candidate_count_violations(ds) == []
        assert ct.referential_integrity_violations(ds) == []
        assert ct.clean_structure_violations(ds) == []
        assert not (tmp_path / "benchmark").exists(), "scale output must not be committed"
