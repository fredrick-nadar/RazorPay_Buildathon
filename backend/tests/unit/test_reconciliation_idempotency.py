"""Phase 2 idempotency property tests (PRD 8.3, Phase 2 gate).

Property-style checks without new dependencies: seeded row reorderings of
every input file must not change the economic output hash; repeated runs
must be economically identical; the same idempotency key must return the
stored run without duplicating rows; and force=True must recompute in place.
"""

from __future__ import annotations

import csv
import random
import shutil
from pathlib import Path

import pytest

from app.importers.ingest import ingest_inputs
from app.persistence.database import Database
from app.reconciliation.detectors import reconcile
from app.reconciliation.totals import control_totals
from app.runs import economic_output_hash, execute_run

REPO_ROOT = Path(__file__).resolve().parents[3]


def _hash_of(inputs: Path) -> str:
    ingest = ingest_inputs(inputs)
    result = reconcile(ingest.records)
    totals = control_totals(ingest.records, list(result.cases))
    return economic_output_hash(ingest, result, totals)


def _shuffled_copy(source: Path, target: Path, seed: int) -> Path:
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target)
    rng = random.Random(seed)
    for path in sorted(target.glob("*.csv")):
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            rows = list(reader)
        header, data = rows[0], rows[1:]
        rng.shuffle(data)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(data)
    return target


class TestReorderInvariance:
    def test_dev_reordering_preserves_economic_output(self, tmp_path: Path) -> None:
        original = REPO_ROOT / "datasets" / "dev" / "inputs"
        baseline = _hash_of(original)
        for seed in (1, 2, 3, 4, 5):
            shuffled = _shuffled_copy(original, tmp_path / f"dev-shuffled-{seed}", seed)
            assert _hash_of(shuffled) == baseline, f"seed {seed} changed the output"

    def test_adversarial_reordering_preserves_economic_output(self, tmp_path: Path) -> None:
        original = REPO_ROOT / "datasets" / "adversarial" / "inputs"
        baseline = _hash_of(original)
        for seed in (11, 12):
            shuffled = _shuffled_copy(original, tmp_path / f"adv-shuffled-{seed}", seed)
            assert _hash_of(shuffled) == baseline


class TestRerunEquality:
    def test_repeated_ingest_reconcile_is_identical(self) -> None:
        inputs = REPO_ROOT / "datasets" / "dev" / "inputs"
        assert _hash_of(inputs) == _hash_of(inputs)

    def test_execute_run_twice_in_fresh_databases_is_identical(self, tmp_path: Path) -> None:
        inputs = REPO_ROOT / "datasets" / "dev" / "inputs"
        first_db = Database(tmp_path / "first.sqlite3")
        try:
            first = execute_run(inputs, first_db)
        finally:
            first_db.close()
        second_db = Database(tmp_path / "second.sqlite3")
        try:
            second = execute_run(inputs, second_db)
        finally:
            second_db.close()
        assert first.economic_output_hash == second.economic_output_hash
        assert first.run_id == second.run_id
        assert not first.reused and not second.reused


class TestIdempotencyKey:
    def test_same_key_returns_stored_run_without_duplicates(self, tmp_path: Path) -> None:
        inputs = REPO_ROOT / "datasets" / "dev" / "inputs"
        database = Database(tmp_path / "idem.sqlite3")
        try:
            first = execute_run(inputs, database)
            assert not first.reused
            second = execute_run(inputs, database)
            assert second.reused
            assert second.run_id == first.run_id
            assert second.economic_output_hash == first.economic_output_hash
            runs = database.query_all("SELECT * FROM runs")
            assert len(runs) == 1
            source_rows = database.query_all("SELECT * FROM source_rows")
            assert len(source_rows) == 282
            matches = database.query_all("SELECT * FROM match_groups")
            first_matches = len(matches)
            third = execute_run(inputs, database)
            assert third.reused
            assert len(database.query_all("SELECT * FROM match_groups")) == first_matches
        finally:
            database.close()

    def test_force_recomputes_in_place(self, tmp_path: Path) -> None:
        inputs = REPO_ROOT / "datasets" / "dev" / "inputs"
        database = Database(tmp_path / "force.sqlite3")
        try:
            first = execute_run(inputs, database)
            forced = execute_run(inputs, database, force=True)
            assert not forced.reused
            assert forced.economic_output_hash == first.economic_output_hash
            assert len(database.query_all("SELECT * FROM runs")) == 1
            assert len(database.query_all("SELECT * FROM cases")) == 12
        finally:
            database.close()


class TestPostCreationFailureSemantics:
    """Unexpected failures after run creation must persist FAILED."""

    def test_unexpected_reconciliation_failure_persists_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.runs as runs_module

        def explode(records: object) -> object:
            raise RuntimeError("unexpected reconciliation crash")

        monkeypatch.setattr(runs_module, "reconcile", explode)
        database = Database(tmp_path / "failed.sqlite3")
        try:
            with pytest.raises(RuntimeError, match="unexpected reconciliation crash"):
                execute_run(REPO_ROOT / "datasets" / "adversarial" / "inputs", database)
            rows = database.query_all("SELECT * FROM runs")
            assert len(rows) == 1
            assert rows[0]["status"] == "FAILED"
            assert rows[0]["economic_output_hash"] is None
            assert rows[0]["finished_at_utc"] is not None
            assert "RuntimeError" in rows[0]["summary_json"]
        finally:
            database.close()

    def test_unexpected_persistence_failure_persists_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.runs as runs_module

        def explode(database: object, run_id: str, result: object, created_at: str) -> None:
            raise RuntimeError("unexpected persistence crash")

        monkeypatch.setattr(runs_module, "_persist_reconciliation", explode)
        database = Database(tmp_path / "failed-persist.sqlite3")
        try:
            with pytest.raises(RuntimeError, match="unexpected persistence crash"):
                execute_run(REPO_ROOT / "datasets" / "adversarial" / "inputs", database)
            rows = database.query_all("SELECT * FROM runs")
            assert rows[0]["status"] == "FAILED"
        finally:
            database.close()


class TestForceReplacementIsFailureSafe:
    """A failed forced recomputation retains the previous completed result."""

    def test_computation_failure_retains_previous_completed_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.runs as runs_module

        inputs = REPO_ROOT / "datasets" / "adversarial" / "inputs"
        database = Database(tmp_path / "force-safe.sqlite3")
        try:
            first = execute_run(inputs, database)
            cases_before = len(database.query_all("SELECT * FROM cases"))

            def explode(records: object) -> object:
                raise RuntimeError("forced recomputation crashed")

            monkeypatch.setattr(runs_module, "reconcile", explode)
            with pytest.raises(RuntimeError, match="forced recomputation crashed"):
                execute_run(inputs, database, force=True)

            rows = database.query_all("SELECT * FROM runs")
            assert len(rows) == 1
            assert rows[0]["status"] == "COMPLETED"
            assert rows[0]["economic_output_hash"] == first.economic_output_hash
            assert len(database.query_all("SELECT * FROM cases")) == cases_before
        finally:
            database.close()

    def test_in_transaction_swap_failure_rolls_back_to_previous_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.runs as runs_module

        inputs = REPO_ROOT / "datasets" / "adversarial" / "inputs"
        database = Database(tmp_path / "force-swap.sqlite3")
        try:
            first = execute_run(inputs, database)
            cases_before = len(database.query_all("SELECT * FROM cases"))
            rows_before = len(database.query_all("SELECT * FROM source_rows"))

            def explode(database: object, run_id: str, result: object, created_at: str) -> None:
                raise RuntimeError("swap crashed after delete")

            # The failure lands INSIDE the swap transaction, after the old
            # rows were deleted: the rollback must restore them.
            monkeypatch.setattr(runs_module, "_persist_reconciliation", explode)
            with pytest.raises(RuntimeError, match="swap crashed after delete"):
                execute_run(inputs, database, force=True)

            rows = database.query_all("SELECT * FROM runs")
            assert len(rows) == 1
            assert rows[0]["status"] == "COMPLETED"
            assert rows[0]["economic_output_hash"] == first.economic_output_hash
            assert len(database.query_all("SELECT * FROM cases")) == cases_before
            assert len(database.query_all("SELECT * FROM source_rows")) == rows_before
        finally:
            database.close()
