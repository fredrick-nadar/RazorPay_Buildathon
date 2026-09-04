"""Migration tests: transactional v1->v2->v3 upgrade and typed failure.

A failed migration rolls back the active version step completely; the caller
receives ``PersistenceMigrationError``, the stored schema version remains at
the prior durable version, no partially-created target tables survive, and
pre-existing metadata stays intact.
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

V3_TABLES = (
    "hypotheses",
    "proofs",
    "corrections",
)

V4_TABLES = (
    "simulated_corrections",
    "approvals",
    "audit_log",
)

V5_TABLES = (
    "gateway_imports",
    "gateway_source_entities",
)

V6_TABLES = ("gateway_demo_evidence",)
V8_TABLES = ("reconciliation_jobs", "reconciliation_job_events")

ALL_RUNTIME_TABLES = (
    *V2_TABLES,
    *V3_TABLES,
    *V4_TABLES,
    *V5_TABLES,
    *V6_TABLES,
    *V8_TABLES,
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
    def test_fresh_database_migrates_to_v9(self, tmp_path: Path) -> None:
        database = Database(tmp_path / "fresh.sqlite3")
        try:
            assert database.schema_version == 9
            assert database.healthcheck() is True
            tables = _table_names(database.path)
            assert set(ALL_RUNTIME_TABLES) <= tables
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
            assert database.schema_version == 9
            assert database.get_meta("tenant_note") == "phase0-metadata"
            assert database.get_meta("schema_version") == "9"
            assert database.healthcheck() is True
            assert set(ALL_RUNTIME_TABLES) <= _table_names(path)
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
            assert second.schema_version == 9
            assert second.get_meta("tenant_note") == "phase0-metadata"
        finally:
            second.close()

    def test_v2_database_upgrades_to_v9_preserving_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "phase2.sqlite3"
        _create_v1_database(path)
        original_chain = migrations._MIGRATION_CHAIN
        try:
            migrations._MIGRATION_CHAIN = original_chain[:1]
            phase2 = Database(path)
            try:
                phase2.execute(
                    "INSERT INTO runs (run_id, idempotency_key, tenant_id, inputs_path,"
                    " inputs_fingerprint, status, economic_output_hash, rule_manifest_json,"
                    " started_at_utc, finished_at_utc, summary_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "run-existing",
                        "key-existing",
                        "tenant",
                        "inputs",
                        "fingerprint",
                        "COMPLETED",
                        "hash",
                        "{}",
                        "2026-01-01T00:00:00Z",
                        "2026-01-01T00:00:01Z",
                        "{}",
                    ),
                )
                assert phase2.schema_version == 2
            finally:
                phase2.close()
        finally:
            migrations._MIGRATION_CHAIN = original_chain

        upgraded = Database(path)
        try:
            assert upgraded.schema_version == 9
            assert set(V3_TABLES) <= _table_names(path)
            assert set(V4_TABLES) <= _table_names(path)
            assert set(V5_TABLES) <= _table_names(path)
            assert set(V6_TABLES) <= _table_names(path)
            assert set(V8_TABLES) <= _table_names(path)
            rows = upgraded.query_all("SELECT run_id FROM runs")
            assert [row["run_id"] for row in rows] == ["run-existing"]
        finally:
            upgraded.close()


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
        assert not (set(ALL_RUNTIME_TABLES) & tables)
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
            assert database.schema_version == 9
            assert database.get_meta("tenant_note") == "phase0-metadata"
        finally:
            database.close()

    def test_failed_v3_migration_rolls_back_to_v2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "phase2.sqlite3"
        _create_v1_database(path)
        original_chain = migrations._MIGRATION_CHAIN
        try:
            migrations._MIGRATION_CHAIN = original_chain[:1]
            phase2 = Database(path)
            phase2.close()
        finally:
            migrations._MIGRATION_CHAIN = original_chain

        def broken_v3() -> tuple[str, ...]:
            return (
                "CREATE TABLE hypotheses (hypothesis_id TEXT PRIMARY KEY)",
                "CREATE TABLE broken_v3 (",
            )

        monkeypatch.setattr(migrations, "_migration_2_to_3_statements", broken_v3)
        with pytest.raises(PersistenceMigrationError):
            Database(path)

        tables = _table_names(path)
        assert "hypotheses" not in tables
        assert not (set(V3_TABLES) & tables)
        conn = sqlite3.connect(str(path))
        try:
            version = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert version == "2"

    def test_failed_v4_migration_rolls_back_to_v3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "phase3.sqlite3"
        _create_v1_database(path)
        original_chain = migrations._MIGRATION_CHAIN
        try:
            migrations._MIGRATION_CHAIN = original_chain[:2]
            phase3 = Database(path)
            phase3.close()
        finally:
            migrations._MIGRATION_CHAIN = original_chain

        def broken_v4() -> tuple[str, ...]:
            return (
                "CREATE TABLE simulated_corrections (correction_id TEXT PRIMARY KEY)",
                "CREATE TABLE broken_v4 (",
            )

        monkeypatch.setattr(migrations, "_migration_3_to_4_statements", broken_v4)
        with pytest.raises(PersistenceMigrationError):
            Database(path)

        tables = _table_names(path)
        assert "simulated_corrections" not in tables
        assert not (set(V4_TABLES) & tables)
        conn = sqlite3.connect(str(path))
        try:
            version = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert version == "3"

    def test_failed_v5_migration_rolls_back_to_v4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "phase4.sqlite3"
        _create_v1_database(path)
        original_chain = migrations._MIGRATION_CHAIN
        try:
            migrations._MIGRATION_CHAIN = original_chain[:3]
            phase4 = Database(path)
            phase4.close()
        finally:
            migrations._MIGRATION_CHAIN = original_chain

        def broken_v5() -> tuple[str, ...]:
            return (
                "CREATE TABLE gateway_imports (import_id TEXT PRIMARY KEY)",
                "CREATE TABLE broken_v5 (",
            )

        monkeypatch.setattr(migrations, "_migration_4_to_5_statements", broken_v5)
        with pytest.raises(PersistenceMigrationError):
            Database(path)

        tables = _table_names(path)
        assert "gateway_imports" not in tables
        assert not (set(V5_TABLES) & tables)
        conn = sqlite3.connect(str(path))
        try:
            version = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert version == "4"

    def test_failed_v6_migration_rolls_back_to_v5(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "phase5.sqlite3"
        _create_v1_database(path)
        original_chain = migrations._MIGRATION_CHAIN
        try:
            migrations._MIGRATION_CHAIN = original_chain[:4]
            phase5 = Database(path)
            phase5.close()
        finally:
            migrations._MIGRATION_CHAIN = original_chain

        def broken_v6() -> tuple[str, ...]:
            return (
                "ALTER TABLE gateway_source_entities ADD COLUMN readiness_state TEXT",
                "CREATE TABLE broken_v6 (",
            )

        monkeypatch.setattr(migrations, "_migration_5_to_6_statements", broken_v6)
        with pytest.raises(PersistenceMigrationError):
            Database(path)

        conn = sqlite3.connect(str(path))
        try:
            version = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(gateway_source_entities)")
            }
        finally:
            conn.close()
        assert version == "5"
        assert "readiness_state" not in columns
        assert "gateway_demo_evidence" not in _table_names(path)
