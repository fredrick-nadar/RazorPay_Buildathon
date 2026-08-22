"""Phase 3 dry-run integration: previews are persisted, financial truth is not."""

from __future__ import annotations

import json
from pathlib import Path

from app.persistence.database import Database
from app.runs import execute_run

REPO_ROOT = Path(__file__).resolve().parents[3]

FINANCIAL_TABLES = (
    "norm_payments",
    "norm_refunds",
    "norm_settlements",
    "norm_bank_entries",
    "norm_ledger_entries",
)


def _rows(database: Database, table: str) -> list[dict]:
    rows = database.query_all(f"SELECT * FROM {table} ORDER BY 1, 2")
    return [dict(row) for row in rows]


def test_force_rerun_keeps_financial_tables_stable_and_persists_previews(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "dry-run.sqlite3")
    try:
        first = execute_run(REPO_ROOT / "datasets" / "dev" / "inputs", database)
        before = {table: _rows(database, table) for table in FINANCIAL_TABLES}
        second = execute_run(REPO_ROOT / "datasets" / "dev" / "inputs", database, force=True)
        after = {table: _rows(database, table) for table in FINANCIAL_TABLES}

        assert second.economic_output_hash == first.economic_output_hash
        assert after == before
        assert database.query_one("SELECT COUNT(*) AS c FROM hypotheses")["c"] == 12
        assert database.query_one("SELECT COUNT(*) AS c FROM proofs")["c"] == 12
        assert database.query_one("SELECT COUNT(*) AS c FROM corrections")["c"] == 9
        assert (
            database.query_one(
                "SELECT COUNT(*) AS c FROM norm_ledger_entries "
                "WHERE entry_origin = 'SIMULATED_CORRECTION'"
            )["c"]
            == 0
        )
        corrections = [dict(row) for row in database.query_all("SELECT * FROM corrections")]
        assert {row["status"] for row in corrections} == {"DRAFT"}
        nonzero = [row for row in corrections if row["proposed_delta_paise"] != 0]
        zero_delta = [row for row in corrections if row["proposed_delta_paise"] == 0]
        assert len(nonzero) == 6
        assert len(zero_delta) == 3
        assert all(json.loads(row["proposed_entry_json"]) for row in nonzero)
        assert all(row["proposed_entry_json"] is None for row in zero_delta)
    finally:
        database.close()
