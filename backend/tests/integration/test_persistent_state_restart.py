"""Persistent state contract: the deployment survives a restart on the same paths.

``db_path`` and ``import_staging_root`` are the entire durable state of an
ARGUS deployment. This test proves the release contract end to end, offline,
in temporary directories only:

1. start on an empty persistence location;
2. migrate the schema to the latest version;
3. create a small synthetic import session and one reconciliation run;
4. shut down cleanly;
5. restart against the same persisted paths;
6. restore the session/import state and the run;
7. produce no duplicate economic effect and no second source activation.

The repository's own ``argus.local.sqlite3`` is never opened here: every path
comes from pytest's ``tmp_path``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.importers.adapters import BANK_SPEC, LEDGER_SPEC
from app.importers.csv_intake import commit_csv_evidence
from app.importers.session_staging import (
    load_manifest,
    resolve_session_dir,
    session_source_status,
)
from app.main import create_app
from app.persistence.database import Database, open_database
from app.persistence.migrations import latest_schema_version
from app.runs import execute_run

SESSION_ID = "restart-contract-session"

# Small, wholly fictional records. Amounts are decimal rupee strings parsed by
# the existing adapters into signed integer paise; nothing here is real data.
PAYMENTS_CSV = (
    "payment_id,order_id,status,currency,gross_amount,fee_amount,tax_amount,"
    "captured_at_utc,settlement_id\n"
    "pay_RESTART000001,order_RESTART01,CAPTURED,INR,1000.00,20.00,3.60,"
    "2026-03-02T03:17:28Z,stl_RESTART0001\n"
    "pay_RESTART000002,order_RESTART02,CAPTURED,INR,2500.00,50.00,9.00,"
    "2026-03-02T04:11:02Z,stl_RESTART0001\n"
)
REFUNDS_CSV = "refund_id,payment_id,status,currency,refund_amount,created_at_utc,settlement_id\n"
SETTLEMENTS_CSV = (
    "settlement_id,settled_at_utc,window_start_utc,window_end_utc,status,currency,"
    "gross_credit,fee_amount,tax_amount,adjustment_amount,net_amount,utr\n"
    "stl_RESTART0001,2026-03-03T04:18:47Z,2026-03-02T00:00:00Z,2026-03-03T00:00:00Z,"
    "PROCESSED,INR,3500.00,70.00,12.60,0.00,3417.40,UTIRESTART000001\n"
)
BANK_CSV = (
    "bank_entry_id,posted_at_utc,value_date,currency,signed_amount,narration,utr,"
    "account_fingerprint\n"
    "bnk_RESTART0001,2026-03-03T04:23:47Z,2026-03-03,INR,3417.40,"
    "NEFT CR UTIRESTART000001 ARGUS SYNTHETIC SETTLEMENT stl_RESTART0001,"
    "UTIRESTART000001,FP-ARGUS-SYNTHETIC-01\n"
)
LEDGER_CSV = (
    "ledger_entry_id,account_code,accounting_date,currency,signed_amount,"
    "source_reference,source_type,description,entry_origin\n"
    "led_RESTART0001,2100-PAYMENTS-CLEARING,2026-03-02,INR,1000.00,pay_RESTART000001,"
    "PAYMENT,Payment captured pay_RESTART000001,IMPORTED\n"
    "led_RESTART0002,2100-PAYMENTS-CLEARING,2026-03-02,INR,2500.00,pay_RESTART000002,"
    "PAYMENT,Payment captured pay_RESTART000002,IMPORTED\n"
)

# The shared intake boundary requires an explicitly reviewed mapping; these
# synthetic files are already canonical, so the review is the identity map.
BANK_MAPPING = {column: column for column in BANK_SPEC.columns}
LEDGER_MAPPING = {column: column for column in LEDGER_SPEC.columns}

INPUT_FILES = {
    "payments.csv": PAYMENTS_CSV,
    "refunds.csv": REFUNDS_CSV,
    "settlements.csv": SETTLEMENTS_CSV,
    "bank_entries.csv": BANK_CSV,
    "ledger_entries.csv": LEDGER_CSV,
}


@pytest.fixture
def persistence(tmp_path: Path) -> tuple[Settings, Path]:
    """Settings whose persistent paths do not exist yet (empty location)."""
    root = tmp_path / "argus-state"
    settings = Settings(
        db_path=root / "db" / "argus.sqlite3",
        import_staging_root=root / "staging",
        telegram_enabled=False,
        _env_file=None,
    )
    assert not settings.db_path.exists()
    assert not settings.import_staging_root.exists()

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    for name, content in INPUT_FILES.items():
        (inputs_dir / name).write_text(content, encoding="utf-8")
    return settings, inputs_dir


def _boot(settings: Settings) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(settings)
    with TestClient(app) as client:
        yield app, client


def _run_count(database: Database) -> int:
    row = database.query_one("SELECT COUNT(*) AS n FROM runs")
    assert row is not None
    return int(row["n"])


def _ledger_row_count(database: Database) -> int:
    row = database.query_one(
        "SELECT COUNT(*) AS n FROM norm_ledger_entries",
    )
    assert row is not None
    return int(row["n"])


def test_restart_on_the_same_paths_restores_state_without_duplicate_effect(
    persistence: tuple[Settings, Path],
) -> None:
    settings, inputs_dir = persistence

    # --- 1/2/3. Empty location -> migration -> synthetic session and run -----
    first_app: FastAPI
    for first_app, _client in _boot(settings):
        database: Database = first_app.state.db
        assert settings.db_path.is_file(), "startup must create only the db parent + file"
        assert settings.import_staging_root.is_dir()
        assert database.schema_version == latest_schema_version()
        assert database.healthcheck() is True

        commit_csv_evidence(
            settings=settings,
            database=database,
            filename="bank.csv",
            content=BANK_CSV,
            file_type="bank_entries",
            session_id=SESSION_ID,
            mapping=BANK_MAPPING,
        )
        commit_csv_evidence(
            settings=settings,
            database=database,
            filename="ledger.csv",
            content=LEDGER_CSV,
            file_type="ledger_entries",
            session_id=SESSION_ID,
            mapping=LEDGER_MAPPING,
        )

        first_run = execute_run(inputs_dir, database)
        first_hash = first_run.economic_output_hash
        first_run_id = first_run.run_id
        assert first_run.reused is False
        assert _run_count(database) == 1
        ledger_rows_before = _ledger_row_count(database)

    session_dir = resolve_session_dir(settings, SESSION_ID, create=False)
    manifest_before = load_manifest(session_dir)
    status_before = session_source_status(session_dir)

    # --- 4. Clean shutdown: the lifespan closed the connection ---------------
    with pytest.raises(sqlite3.ProgrammingError):
        database.query_one("SELECT 1")

    # --- 5/6. Restart against the SAME persisted paths -----------------------
    for second_app, _client in _boot(settings):
        restarted: Database = second_app.state.db
        assert restarted.path == settings.db_path
        assert restarted.schema_version == latest_schema_version()

        restored_manifest = load_manifest(session_dir)
        restored_status = session_source_status(session_dir)
        assert restored_manifest == manifest_before, "import session state must be restored"
        assert restored_status == status_before

        stored = restarted.query_one(
            "SELECT run_id, status FROM runs WHERE run_id = ?", (first_run_id,)
        )
        assert stored is not None, "the completed run must survive the restart"

        # --- 7. No duplicate economic effect, no second activation ----------
        replay = execute_run(inputs_dir, restarted)
        assert replay.reused is True
        assert replay.run_id == first_run_id
        assert replay.economic_output_hash == first_hash
        assert _run_count(restarted) == 1
        assert _ledger_row_count(restarted) == ledger_rows_before

        reactivation = commit_csv_evidence(
            settings=settings,
            database=restarted,
            filename="bank.csv",
            content=BANK_CSV,
            file_type="bank_entries",
            session_id=SESSION_ID,
            mapping=BANK_MAPPING,
        )
        assert reactivation["reused"] is True, "identical bytes must not create a new revision"
        assert load_manifest(session_dir) == manifest_before


def test_startup_creates_only_the_required_parent_directories(tmp_path: Path) -> None:
    root = tmp_path / "nested" / "deployment"
    settings = Settings(
        db_path=root / "data" / "argus.sqlite3",
        import_staging_root=root / "evidence",
        _env_file=None,
    )
    database = open_database(settings)
    try:
        assert (root / "data").is_dir()
        assert (root / "evidence").is_dir()
        # Nothing else beside the two declared persistence targets.
        assert sorted(child.name for child in root.iterdir()) == ["data", "evidence"]
    finally:
        database.close()


def test_migration_is_ordered_and_idempotent_across_repeated_boots(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "state" / "argus.sqlite3",
        import_staging_root=tmp_path / "state" / "staging",
        _env_file=None,
    )
    versions: list[int] = []
    for _ in range(3):
        database = open_database(settings)
        try:
            versions.append(database.schema_version)
            assert database.healthcheck() is True
        finally:
            database.close()
    assert versions == [latest_schema_version()] * 3


def test_persistence_summary_reports_the_paths_that_must_survive(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "argus.sqlite3",
        import_staging_root=tmp_path / "staging",
        _env_file=None,
    )
    summary = settings.persistence_summary()
    assert summary["db_exists"] is False
    assert summary["import_staging_root_exists"] is False
    assert Path(str(summary["db_path_resolved"])).is_absolute()
    assert Path(str(summary["import_staging_root_resolved"])).is_absolute()


def test_empty_or_directory_persistence_paths_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ARGUS_DB_PATH must not be empty"):
        Settings(db_path=Path(" "), _env_file=None)
    a_directory = tmp_path / "not-a-db"
    a_directory.mkdir()
    with pytest.raises(ValueError, match="points at a directory"):
        Settings(db_path=a_directory, _env_file=None)
    a_file = tmp_path / "not-a-tree"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="points at a file"):
        Settings(import_staging_root=a_file, _env_file=None)
