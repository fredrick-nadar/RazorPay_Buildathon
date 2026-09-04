"""Independent merchant intake; all fixtures are fictional and network-free."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.routes_ingest import validate_canonical_rows
from app.config import Settings
from app.importers.demo_settlement import build_demo_evidence
from app.importers.session_staging import resolve_session_dir, stage_source_revision
from app.main import create_app
from app.persistence import migrations
from app.persistence.database import Database, PersistenceMigrationError
from app.persistence.gateway_imports import (
    GatewayEntity,
    persist_gateway_snapshot,
    record_demo_evidence,
)

PAYMENT = {
    "id": "pay_scope_test",
    "order_id": "order_scope_test",
    "status": "captured",
    "amount": 10000,
    "fee": 236,
    "tax": 36,
    "currency": "INR",
    "created_at": 1772437000,
}
SESSION = "gateway_only_test"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "scope.sqlite3",
        import_staging_root=tmp_path / "i",
        _env_file=None,
    )


def _seed(db: Database) -> str:
    return persist_gateway_snapshot(
        db,
        provider="RAZORPAY",
        mode="TEST",
        credential_identifier="fictional-fixture",
        entities=[
            GatewayEntity(
                entity_type="ORDER",
                entity_id="order_scope_test",
                payload={"id": "order_scope_test", "status": "paid"},
                reconciliation_eligible=False,
                exclusion_reason="ORDER_IS_NOT_A_PAYMENT",
            ),
            GatewayEntity(
                entity_type="PAYMENT",
                entity_id=PAYMENT["id"],
                payload=PAYMENT,
                reconciliation_eligible=True,
                readiness_state="AWAITING_RAZORPAY_SETTLEMENT",
            ),
        ],
    ).import_id


def _upload(client: TestClient, source: str, content: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/ingest/commit-csv",
        json={
            "session_id": SESSION,
            "filename": f"synthetic-merchant-{source}.csv",
            "file_type": source,
            "content": content,
            "mappings": [
                {"target_field": field, "source_column": field}
                for field in content.splitlines()[0].split(",")
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _status(client: TestClient) -> dict[str, Any]:
    response = client.get(f"/api/v1/ingest/sessions/{SESSION}/status")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("merchant_first", [False, True])
@pytest.mark.parametrize("endpoint", ["generate-gateway-evidence", "generate-demo-evidence"])
def test_separate_uploads_readiness_idempotency_and_snapshot_counts(
    tmp_path: Path, merchant_first: bool, endpoint: str
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        import_id = _seed(app.state.db)
        url = f"/api/v1/razorpay/imports/{import_id}"
        before = client.get(url).json()
        # Offline fixture generation is not an API action or merchant evidence.
        fixtures = build_demo_evidence(
            import_id=import_id, payments=[PAYMENT], refunds=[], include_merchant_sources=True
        )["files"]
        assert _status(client)["ready_source_groups"] == 0
        if merchant_first:
            _upload(client, "bank_entries", fixtures["bank_entries.csv"])
            _upload(client, "ledger_entries", fixtures["ledger_entries.csv"])
            prior = _status(client)["active_sources"]
            assert _status(client)["ready_source_groups"] == 2
            assert _status(client)["settlement_reconciliation_required"] is True
            assert _status(client)["ready"] is False

        demo = client.post(url + "/" + endpoint, json={"session_id": SESSION})
        assert demo.status_code == 200, demo.text
        assert demo.json()["scope"] == "GATEWAY_ONLY"
        assert set(demo.json()["source_revisions"]) == {"payments", "refunds", "settlements"}
        assert "bank" not in " ".join(demo.json()["source_revisions"])
        status = _status(client)
        if merchant_first:
            assert all(status["active_sources"][key] == value for key, value in prior.items())
        else:
            assert status["ready_source_groups"] == 1
            assert status["ready"] is False
            assert set(status["active_sources"]) == {"payments", "refunds", "settlements"}
            blocked = client.post("/api/v1/ingest/reconcile-session", json={"session_id": SESSION})
            assert blocked.status_code == 409
            _upload(client, "bank_entries", fixtures["bank_entries.csv"])
            assert _status(client)["ready_source_groups"] == 2
            assert _status(client)["ready"] is False
            _upload(client, "ledger_entries", fixtures["ledger_entries.csv"])

        complete = _status(client)
        assert complete["ready_source_groups"] == 3
        assert complete["ready"] is True
        repeated = client.post(url + "/" + endpoint, json={"session_id": SESSION})
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["reused"] is True
        assert repeated.json()["evidence_id"] == demo.json()["evidence_id"]
        assert _status(client)["active_sources"] == complete["active_sources"]
        assert _status(client)["revision_counts"] == complete["revision_counts"]

        after = client.get(url, params={"session_id": SESSION}).json()
        assert after["counts"] == before["counts"] == {"ORDER": 1, "PAYMENT": 1}
        assert after["counts"].get("SETTLEMENT", 0) == 0
        assert after["counts"].get("SETTLEMENT_RECON", 0) == 0
        assert after["source_records_count"] == before["source_records_count"] == 2
        assert after["demo_evidence"]["activation_state"] == "ACTIVE"
        assert after["demo_evidence"]["expected_sources"] == ["payments", "refunds", "settlements"]
        result = client.post(
            "/api/v1/ingest/reconcile-session", json={"session_id": SESSION, "mode": "rules-only"}
        )
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "COMPLETED"

    with TestClient(create_app(settings)) as restarted:
        assert _status(restarted)["ready_source_groups"] == 3
        restored = restarted.get(url, params={"session_id": SESSION}).json()
        assert restored["demo_evidence"]["scope"] == "GATEWAY_ONLY"
        assert restored["demo_evidence"]["activation_state"] == "ACTIVE"
        assert restored["counts"] == before["counts"]


def _seed_v6(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, dict[str, Any]]:
    with monkeypatch.context() as patch:
        # Version-anchored, not length-relative: adding a later migration must
        # not silently change which schema this fixture seeds.
        patch.setattr(
            migrations,
            "_MIGRATION_CHAIN",
            tuple(link for link in migrations._MIGRATION_CHAIN if link[1] <= 6),
        )
        db = Database(settings.db_path)
        try:
            assert db.schema_version == 6
            import_id = _seed(db)
            bundle = build_demo_evidence(
                import_id=import_id, payments=[PAYMENT], refunds=[], include_merchant_sources=True
            )
            # Exact v6 record layout; production migrations must preserve every field.
            db.execute(
                "INSERT INTO gateway_demo_evidence VALUES (?, ?, ?, ?, ?)",
                (
                    "demo-legacy",
                    import_id,
                    SESSION,
                    bundle["manifest_hash"],
                    "2026-03-02T00:00:00Z",
                ),
            )
            return import_id, "demo-legacy", bundle
        finally:
            db.close()


def test_v6_full_demo_history_is_preserved_and_requires_merchant_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    import_id, old_id, bundle = _seed_v6(settings, monkeypatch)
    directory = resolve_session_dir(settings, SESSION, create=True)
    for filename, content in bundle["files"].items():
        source = filename.removesuffix(".csv")
        accepted, quarantined, _ = validate_canonical_rows(content, source)
        stage_source_revision(
            session_dir=directory,
            source_type=source,
            original_filename=filename,
            raw_content=json.dumps(
                {
                    "provenance": "SYNTHETIC_DEMO",
                    "derived_from_gateway_import": import_id,
                    "manifest_hash": bundle["manifest_hash"],
                    "canonical_filename": filename,
                }
            ),
            canonical_csv=content,
            accepted_count=accepted,
            quarantined_count=quarantined,
            origin="SYNTHETIC_DEMO",
            external_import_id=import_id,
        )
    app = create_app(settings)
    with TestClient(app) as client:
        url = f"/api/v1/razorpay/imports/{import_id}"
        old = client.get(url, params={"session_id": SESSION}).json()["demo_evidence"]
        assert old["scope"] == "FULL_DEMO"
        assert old["evidence_id"] == old_id
        assert old["activation_state"] == "ACTIVE"
        assert old["created_at_utc"] == "2026-03-02T00:00:00Z"
        prior = _status(client)
        assert prior["ready_source_groups"] == 1
        assert prior["merchant_upload_required"] == ["bank_entries", "ledger_entries"]
        blocked = client.post("/api/v1/ingest/reconcile-session", json={"session_id": SESSION})
        assert blocked.status_code == 409
        assert "separate merchant upload required" in blocked.json()["detail"]

        upgrade = client.post(url + "/generate-gateway-evidence", json={"session_id": SESSION})
        assert upgrade.status_code == 200, upgrade.text
        assert upgrade.json()["evidence_id"] != old_id
        for source in ("bank_entries", "ledger_entries"):
            assert _status(client)["active_sources"][source] == prior["active_sources"][source]
            _upload(client, source, bundle["files"][f"{source}.csv"])
        assert _status(client)["ready"] is True
        assert _status(client)["merchant_upload_required"] == []
        new = client.get(url, params={"session_id": SESSION}).json()["demo_evidence"]
        assert new["scope"] == "GATEWAY_ONLY"
        assert new["activation_state"] == "ACTIVE"
        history = app.state.db.query_all("SELECT * FROM gateway_demo_evidence ORDER BY scope")
        assert len(history) == 2
        assert history[0]["evidence_id"] == old_id
        assert history[0]["manifest_hash"] == bundle["manifest_hash"]
        assert history[0]["created_at_utc"] == old["created_at_utc"]


def test_failed_scope_migration_preserves_v6_history_and_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    import_id, old_id, bundle = _seed_v6(settings, monkeypatch)
    real_statements = migrations._migration_6_to_7_statements
    with monkeypatch.context() as patch:
        patch.setattr(
            migrations,
            "_migration_6_to_7_statements",
            lambda: (*real_statements(), "CREATE TABLE intentionally_broken ("),
        )
        with pytest.raises(PersistenceMigrationError):
            Database(settings.db_path)
    with sqlite3.connect(settings.db_path) as conn:
        assert (
            conn.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()[0]
            == "6"
        )
        assert conn.execute("SELECT * FROM gateway_demo_evidence").fetchone() == (
            old_id,
            import_id,
            SESSION,
            bundle["manifest_hash"],
            "2026-03-02T00:00:00Z",
        )
    db = Database(settings.db_path)
    try:
        assert db.schema_version == 9
        row = db.query_one("SELECT * FROM gateway_demo_evidence")
        assert row is not None and row["scope"] == "FULL_DEMO"
        new_id, reused = record_demo_evidence(
            db,
            import_id=import_id,
            session_id=SESSION,
            manifest_hash="new-scope-hash",
            scope="GATEWAY_ONLY",
        )
        assert new_id != old_id and not reused
    finally:
        db.close()
