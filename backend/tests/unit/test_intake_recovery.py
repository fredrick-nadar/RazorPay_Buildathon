"""Fault injection against isolated fictional intake; never calls Razorpay."""

import json
import multiprocessing
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.importers import intake_activation, session_staging
from app.importers.demo_settlement import build_demo_evidence
from app.main import create_app
from app.persistence import intake_activation as projection
from app.persistence.gateway_imports import get_demo_evidence
from tests.unit.test_gateway_only_demo import PAYMENT, SESSION, _seed, _settings, _upload


@pytest.mark.parametrize("failure_write", range(1, 7))
def test_failed_third_source_does_not_activate_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_write: int
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        import_id = _seed(app.state.db)
        fixtures = build_demo_evidence(
            import_id=import_id, payments=[PAYMENT], refunds=[], include_merchant_sources=True
        )["files"]
        for source in ("bank_entries", "ledger_entries"):
            _upload(client, source, fixtures[f"{source}.csv"])
        directory = session_staging.resolve_session_dir(settings, SESSION, create=True)
        before = session_staging.load_manifest(directory)
        original = session_staging._write_immutable
        calls = 0

        def fail_third(path: Path, content: bytes) -> None:
            nonlocal calls
            calls += 1
            if calls == failure_write:
                raise OSError("injected third-source disk failure")
            original(path, content)

        with monkeypatch.context() as patch:
            patch.setattr(session_staging, "_write_immutable", fail_third)
            response = client.post(
                f"/api/v1/razorpay/imports/{import_id}/generate-gateway-evidence",
                json={"session_id": SESSION},
            )
        assert response.status_code >= 400
        assert session_staging.load_manifest(directory) == before
        retry = client.post(
            f"/api/v1/razorpay/imports/{import_id}/generate-gateway-evidence",
            json={"session_id": SESSION},
        )
        assert retry.status_code == 200, retry.text
        after = session_staging.load_manifest(directory)
        for source in ("bank_entries", "ledger_entries"):
            assert after["active_by_type"][source] == before["active_by_type"][source]


def test_db_and_audit_failure_recovers_after_restart_without_reactivating_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        import_id = _seed(app.state.db)
        url = f"/api/v1/razorpay/imports/{import_id}"
        with monkeypatch.context() as patch:

            def fail_audit(*args: Any, **kwargs: Any) -> None:
                raise sqlite3.OperationalError("injected disk failure")

            patch.setattr(projection, "record_audit_event", fail_audit)
            response = client.post(url + "/generate-gateway-evidence", json={"session_id": SESSION})
            assert response.status_code == 503
            assert response.json()["code"] == "ACTIVATION_RECOVERY_PENDING"
            assert get_demo_evidence(app.state.db, import_id=import_id, session_id=SESSION) is None
        directory = session_staging.resolve_session_dir(settings, SESSION, create=False)
        manifest = session_staging.load_manifest(directory)
        assert set(manifest["active_by_type"]) == {"payments", "refunds", "settlements"}
        assert len(manifest["activation_receipts"]) == 1
        # A later independent upload must NEVER be undone by replaying the old receipt.
        row = manifest["revisions"][manifest["active_by_type"]["settlements"]]
        content = (directory / row["canonical_path"]).read_text(encoding="utf-8")
        _upload(client, "settlements", content)
        selected = session_staging.load_manifest(directory)["active_by_type"]

    restarted = create_app(settings)
    with TestClient(restarted) as client:
        for _ in range(2):
            restored = client.get(url, params={"session_id": SESSION})
            assert restored.status_code == 200, restored.text
            assert restored.json()["demo_evidence"]["activation_state"] == "PARTIALLY_ACTIVE"
        assert session_staging.load_manifest(directory)["active_by_type"] == selected
        rows = restarted.state.db.query_all(
            "SELECT event_id FROM audit_log WHERE action = 'SYNTHETIC_DEMO_EVIDENCE_STAGED'"
        )
        assert len(rows) == 1


def _exit_after_manifest(settings_path: str, import_id: str) -> None:
    settings = _settings(Path(settings_path))

    def crash(*args: Any, **kwargs: Any) -> None:
        os._exit(23)

    intake_activation.project_activation = crash
    with TestClient(create_app(settings)) as client:
        client.post(
            f"/api/v1/razorpay/imports/{import_id}/generate-gateway-evidence",
            json={"session_id": SESSION},
        )


def test_process_death_after_switch_is_recovered_on_session_read(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app):
        import_id = _seed(app.state.db)
    process = multiprocessing.get_context("spawn").Process(
        target=_exit_after_manifest, args=(str(tmp_path), import_id)
    )
    process.start()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("crash worker did not finish")
    assert process.exitcode == 23
    restarted = create_app(settings)
    with TestClient(restarted) as client:
        status = client.get(f"/api/v1/ingest/sessions/{SESSION}/status")
        assert status.status_code == 200, status.text
        assert status.json()["ready_source_groups"] == 1
        assert get_demo_evidence(restarted.state.db, import_id=import_id, session_id=SESSION)


def _source(
    source: session_staging.SourceType, content: str = "id\none\n"
) -> session_staging.SourceRevisionInput:
    return session_staging.SourceRevisionInput(
        source, "very-long-original-name-" * 12 + ".csv", content, content, 1, 0, "MANUAL_CSV"
    )


def test_manifest_replace_failure_and_retry_leave_no_partial_immutable_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = session_staging.os.replace

    def fail_replace(src: Any, dst: Any) -> None:
        if Path(dst).name == session_staging.MANIFEST_FILENAME:
            raise OSError("injected replace failure")
        real_replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(session_staging.os, "replace", fail_replace)
        with pytest.raises(OSError):
            session_staging.stage_source_bundle(session_dir=tmp_path, sources=[_source("payments")])
    assert session_staging.load_manifest(tmp_path)["active_by_type"] == {}
    assert not list(tmp_path.rglob(".w-*"))
    session_staging.stage_source_bundle(session_dir=tmp_path, sources=[_source("payments")])
    assert "payments" in session_staging.verified_active_sources(tmp_path)


def test_compact_paths_and_immutable_snapshot_survive_later_upload(tmp_path: Path) -> None:
    source = _source("bank_entries")
    session_staging.stage_source_bundle(session_dir=tmp_path, sources=[source])
    old = session_staging.snapshot_active_sources(tmp_path, empty_refunds="refund_id\n")
    session_staging.stage_source_bundle(
        session_dir=tmp_path, sources=[_source("bank_entries", "id\ntwo\n")]
    )
    new = session_staging.snapshot_active_sources(tmp_path, empty_refunds="refund_id\n")
    assert old != new
    assert (old / "bank_entries.csv").read_text() == "id\none\n"
    assert (new / "bank_entries.csv").read_text() == "id\ntwo\n"
    for row in session_staging.load_manifest(tmp_path)["revisions"].values():
        assert len(row["raw_path"]) < 40
        assert row["original_filename"] == source.original_filename
    # Root derived files are never trusted as run inputs.
    (tmp_path / "bank_entries.csv").write_text("tampered derived file")
    assert session_staging.snapshot_active_sources(tmp_path, empty_refunds="refund_id\n") == new
    assert (
        json.loads((old / ".evidence.json").read_text())["bank_entries"]["origin"] == "MANUAL_CSV"
    )


def _lock_worker(directory: str, entered: Any) -> None:
    with session_staging.session_lock(Path(directory)):
        entered.set()


def test_os_lock_prevents_another_process_entering_session(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    process = context.Process(target=_lock_worker, args=(str(tmp_path), entered))
    # Reentrant in this thread but exclusive across processes.
    with session_staging.session_lock(tmp_path), session_staging.session_lock(tmp_path):
        process.start()
        assert not entered.wait(1)
    assert entered.wait(10)
    process.join(5)
    assert process.exitcode == 0


def test_run_retry_reuses_completed_result(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        import_id = _seed(app.state.db)
        fixtures = build_demo_evidence(
            import_id=import_id, payments=[PAYMENT], refunds=[], include_merchant_sources=True
        )["files"]
        client.post(
            f"/api/v1/razorpay/imports/{import_id}/generate-gateway-evidence",
            json={"session_id": SESSION},
        ).raise_for_status()
        for source in ("bank_entries", "ledger_entries"):
            _upload(client, source, fixtures[f"{source}.csv"])
        payload = {"session_id": SESSION, "mode": "rules-only"}
        first = client.post("/api/v1/ingest/reconcile-session", json=payload)
        second = client.post("/api/v1/ingest/reconcile-session", json=payload)
        assert first.status_code == second.status_code == 200
        assert first.json()["run_id"] == second.json()["run_id"]
        assert second.json()["reused"] is True


def test_tampered_activation_receipt_is_a_permanent_integrity_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        import_id = _seed(app.state.db)
        client.post(
            f"/api/v1/razorpay/imports/{import_id}/generate-gateway-evidence",
            json={"session_id": SESSION},
        ).raise_for_status()
        directory = session_staging.resolve_session_dir(settings, SESSION, create=False)
        manifest = session_staging.load_manifest(directory)
        receipt = next(iter(manifest["activation_receipts"].values()))
        receipt["scope"] = "FULL_DEMO"
        session_staging._write_manifest(directory, manifest)
        response = client.get(f"/api/v1/ingest/sessions/{SESSION}/status")
        assert response.status_code == 409
        assert "invalid or conflicts" in response.json()["detail"]
