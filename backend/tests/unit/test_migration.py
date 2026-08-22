"""Phase 2 migration tests: transactional v1->v2 upgrade and typed failure.

A failed migration happens before any Phase 2 table exists, so no run row
can be created or marked FAILED; the caller receives
``PersistenceMigrationError``, the stored schema version remains 1, no v2
table survives, and pre-existing metadata stays intact.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.persistence import migrations
from app.persistence.database import Database, PersistenceMigrationError
from app.persistence.migrations import BASELINE_SCHEMA_V1

V2_TABLES = (
    "runs",
    "source_rows",
    "norm_payments",
    "norm_refunds",
    "norm_settlements",
    "norm_bank_entries",
    "norm_ledger_entries",
    "match_groups",
    "match_members",
    "cases",
    "case_evidence",
)


def _table_names(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {str(row[0]) for row in rows}
    finally:
        conn.close()


def _create_v1_database(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(BASELINE_SCHEMA_V1)
        conn.execute("INSERT INTO app_meta(key, value) VALUES ('schema_version', '1')")
        conn.execute("INSERT INTO app_meta(key, value) VALUES ('tenant_note', 'phase0-metadata')")
        conn.commit()
    finally:
        conn.close()


class TestFreshDatabase:
    def test_fresh_database_migrates_to_v2(self, tmp_path: Path) -> None:
        database = Database(tmp_path / "fresh.sqlite3")
        try:
            assert database.schema_version == 2
            assert database.healthcheck() is True
            tables = _table_names(database.path)
            assert set(V2_TABLES) <= tables
            assert "app_meta" in tables
        finally:
            database.close()

    def test_fresh_database_runs_table_starts_empty(self, tmp_path: Path) -> None:
        database = Database(tmp_path / "fresh.sqlite3")
        try:
            rows = database.query_all("SELECT * FROM runs")
            assert rows == []
        finally:
            database.close()


class TestUpgradeFromV1:
    def test_v1_database_upgrades_without_losing_metadata(self, tmp_path: Path) -> None:
        path = tmp_path / "legacy.sqlite3"
        _create_v1_database(path)
        database = Database(path)
        try:
            assert database.schema_version == 2
            assert database.get_meta("tenant_note") == "phase0-metadata"
            assert database.get_meta("schema_version") == "2"
            assert database.healthcheck() is True
            assert set(V2_TABLES) <= _table_names(path)
            # No run row exists; none is claimed.
            assert database.query_all("SELECT * FROM runs") == []
        finally:
            database.close()

    def test_upgrade_is_idempotent_on_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "legacy.sqlite3"
        _create_v1_database(path)
        first = Database(path)
        first.close()
        second = Database(path)
        try:
            assert second.schema_version == 2
            assert second.get_meta("tenant_note") == "phase0-metadata"
        finally:
            second.close()


class TestMigrationFailure:
    def test_failed_migration_rolls_back_completely(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "legacy.sqlite3"
        _create_v1_database(path)

        def broken_statements() -> tuple[str, ...]:
            # A valid statement followed by a broken one: the rollback must
            # remove the partially created table, proving the transaction
            # boundary rather than "failure before any DDL".
            return (
                "CREATE TABLE runs (run_id TEXT PRIMARY KEY)",
                "CREATE TABLE broken (",
            )

        monkeypatch.setattr(migrations, "_migration_1_to_2_statements", broken_statements)
        with pytest.raises(PersistenceMigrationError):
            Database(path)

        tables = _table_names(path)
        assert "runs" not in tables
        assert not (set(V2_TABLES) & tables)
        conn = sqlite3.connect(str(path))
        try:
            version = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            note = conn.execute("SELECT value FROM app_meta WHERE key = 'tenant_note'").fetchone()[
                0
            ]
        finally:
            conn.close()
        assert version == "1"
        assert note == "phase0-metadata"

    def test_failed_migration_is_retryable_after_fix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "legacy.sqlite3"
        _create_v1_database(path)

        def broken() -> tuple[str, ...]:
            return ("CREATE TABLE definitely_broken (",)

        monkeypatch.setattr(migrations, "_migration_1_to_2_statements", broken)
        with pytest.raises(PersistenceMigrationError):
            Database(path)
        monkeypatch.undo()

        database = Database(path)
        try:
            assert database.schema_version == 2
            assert database.get_meta("tenant_note") == "phase0-metadata"
        finally:
            database.close()
