"""Known-fixture checks, not a frozen benchmark or live model evaluation."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import runpy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.importers.session_staging import resolve_session_dir, stage_source_revision
from app.main import create_app

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "demo_scenarios" / "rzp_companions_v1"
builder = runpy.run_path(str(ROOT / "scripts" / "build_companion_scenario.py"))
checker = runpy.run_path(str(ROOT / "scripts" / "verify_companion_scenario.py"))


def gateway() -> dict[str, list[dict[str, str]]]:
    result = {}
    for source in ("payments", "refunds", "settlements"):
        with (FIXTURE / "inputs" / f"{source}.csv").open(encoding="utf-8", newline="") as handle:
            result[source] = list(csv.DictReader(handle))
    return result


def test_scenario_is_reproducible_and_never_changes_gateway() -> None:
    source = gateway()
    before = copy.deepcopy(source)
    first = builder["scenario"](source, 20260903)
    assert source == before
    assert first == builder["scenario"](source, 20260903)
    assert first == builder["scenario"](
        {key: list(reversed(rows)) for key, rows in source.items()}, 20260903
    )
    assert first != builder["scenario"](source, 20260904)


def test_exported_rows_equal_generated_business_events() -> None:
    generated = builder["scenario"](gateway(), 20260903)
    for source in ("bank_entries", "ledger_entries"):
        with (FIXTURE / "inputs" / f"{source}.csv").open(encoding="utf-8", newline="") as handle:
            assert list(csv.DictReader(handle)) == generated["rows"][source]


def test_frozen_snapshot_reproduces_without_a_browser_session() -> None:
    snapshot = builder["prepare_snapshot"](FIXTURE, 20260903)
    assert snapshot["rows"] == builder["scenario"](gateway(), 20260903)["rows"]
    assert snapshot["import_id"] == "gwi-38a22e8d7367bac0af9d"


def test_known_scenario_detects_every_expected_case_and_preserves_rows() -> None:
    result = checker["verify"](FIXTURE)
    assert result["pass"], result
    assert (
        result["raw_rows"]
        == result["eligible_records"] + result["quarantined_rows"] + result["duplicate_deliveries"]
    )
    assert result["unexpected_cases"] == []
    assert result["provider_id"] == "fake-deterministic-v1"


def test_small_snapshot_fails_instead_of_fabricating_more_payments() -> None:
    with pytest.raises(ValueError, match="requires at least"):
        builder["scenario"]({"payments": [], "refunds": [], "settlements": []}, 1)


def test_csv_route(tmp_path: Path) -> None:
    """Exercise merchant uploads and reconciliation without the user's DB or keys."""
    settings = Settings(
        db_path=tmp_path / "s.db",
        import_staging_root=tmp_path / "i",
        ai_provider="fake",
        _env_file=None,
    )
    session_id = "companion"
    session_dir = resolve_session_dir(settings, session_id, create=True)
    assert session_dir is not None
    import_id = "gwi-38a22e8d7367bac0af9d"
    manifest_hash = hashlib.sha256((FIXTURE / "manifest.json").read_bytes()).hexdigest()
    for source, rows in gateway().items():
        content = (FIXTURE / "inputs" / f"{source}.csv").read_text(encoding="utf-8")
        stage_source_revision(
            session_dir=session_dir,
            source_type=source,
            original_filename=f"{source}.csv",
            raw_content=json.dumps(
                {
                    "provenance": "SYNTHETIC_DEMO",
                    "derived_from_gateway_import": import_id,
                    "manifest_hash": manifest_hash,
                    "canonical_filename": f"{source}.csv",
                }
            ),
            canonical_csv=content,
            accepted_count=len(rows),
            quarantined_count=0,
            origin="SYNTHETIC_DEMO",
            external_import_id=import_id,
        )
    with TestClient(create_app(settings)) as client:
        for source in ("bank_entries", "ledger_entries"):
            content = (FIXTURE / "inputs" / f"{source}.csv").read_text(encoding="utf-8")
            columns = content.splitlines()[0].split(",")
            response = client.post(
                "/api/v1/ingest/commit-csv",
                json={
                    "session_id": session_id,
                    "file_type": source,
                    "filename": f"{source}.csv",
                    "content": content,
                    "mappings": [{"target_field": col, "source_column": col} for col in columns],
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["quarantined_count"] == (1 if source == "bank_entries" else 0)
        response = client.post(
            "/api/v1/ingest/reconcile-session", json={"session_id": session_id, "mode": "agent"}
        )
        assert response.status_code == 200, response.text
        summary = response.json()["summary"]
        assert summary["provider_id"] == "fake-deterministic-v1"
        expected = checker["verify"](FIXTURE)
        assert summary["cases_by_category"] == expected["cases_by_category"]
        assert summary["raw_row_count"] == expected["raw_rows"]
        assert summary["quarantined_row_count"] == 1
        assert summary["duplicate_delivery_count"] == 1
        assert summary["economic_output_hash"] == expected["economic_output_hash"]
