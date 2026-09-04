"""Durable reconciliation controller tests; no network or paid model calls."""

from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import BatchStatus
from app.importers.session_staging import resolve_session_dir, stage_source_revision
from app.investigator.provider import FakeProvider
from app.main import create_app
from app.persistence.database import Database
from app.runs import RunResult
from app.workflow.controller import ReconciliationController

# The worker re-resolves the investigator and refuses to run a job whose
# execution policy has changed, so a job must be created with the same
# fingerprint the resolver would produce.
FAKE_POLICY_FINGERPRINT = FakeProvider().policy_fingerprint


def _settings(tmp_path: Path, **updates: Any) -> Settings:
    return Settings(
        db_path=tmp_path / "controller.sqlite3",
        import_staging_root=tmp_path / "imports",
        ai_provider="fake",
        _env_file=None,
        **updates,
    )


def _result(run_id: str = "run-controlled") -> RunResult:
    return RunResult(
        run_id=run_id,
        status=BatchStatus.COMPLETED,
        reused=False,
        idempotency_key="run-key",
        economic_output_hash="economic-hash",
        summary={"mode": "agent", "provider_id": "fake-deterministic-v1"},
    )


def test_duplicate_start_reuses_one_persisted_job_and_snapshot(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.db_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    calls: list[dict[str, Any]] = []

    def runner(**kwargs: Any) -> RunResult:
        calls.append(kwargs)
        return _result()

    controller = ReconciliationController(db, settings, run_executor=runner, background=False)
    try:
        args = {
            "session_id": "session-a",
            "snapshot_path": snapshot,
            "snapshot_manifest": {"active_sources": {"payments": {"revision_id": "rev-1"}}},
            "requested_mode": "fake",
            "execution_mode": "agent",
            "provider_id": "fake-deterministic-v1",
            "simulated": True,
            "policy_fingerprint": FAKE_POLICY_FINGERPRINT,
        }
        first, first_reused = controller.create_job(**args)
        second, second_reused = controller.create_job(**args)
        assert first_reused is False
        assert second_reused is True
        assert first["job_id"] == second["job_id"]

        completed = controller.run_once(first["job_id"])
        assert completed["status"] == "SUCCEEDED"
        assert completed["run_id"] == "run-controlled"
        assert completed["simulated"] is True
        assert completed["progress"]["kind"] == "STEP_COMPLETION"
        assert completed["progress"]["completed_steps"] == 5
        assert completed["progress"]["total_steps"] == 5
        assert {step["state"] for step in completed["progress"]["steps"]} == {"COMPLETE"}
        assert completed["recovery"] == {
            "retryable": False,
            "remaining_attempts": 1,
            "action": "OPEN_RUN",
        }
        assert [event["event_type"] for event in completed["events"]] == [
            "CREATED",
            "ATTEMPT_STARTED",
            "RUN_LINKED",
        ]
        assert len(calls) == 1
        assert calls[0]["inputs_dir"] == snapshot
    finally:
        controller.close()
        db.close()


def test_concurrent_duplicate_starts_create_one_job(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.db_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    controller = ReconciliationController(db, settings, background=False)
    args = {
        "session_id": "session-concurrent",
        "snapshot_path": snapshot,
        "snapshot_manifest": {"active_sources": {"payments": {"revision_id": "rev-1"}}},
        "requested_mode": "fake",
        "execution_mode": "agent",
        "provider_id": "fake-deterministic-v1",
        "simulated": True,
    }
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: controller.create_job(**args), range(8)))

        job_ids = {job["job_id"] for job, _ in results}
        reused = [was_reused for _, was_reused in results]
        assert len(job_ids) == 1
        assert reused.count(False) == 1
        assert reused.count(True) == 7
        job = controller.get_job(job_ids.pop())
        assert job is not None
        assert [event["event_type"] for event in job["events"]] == ["CREATED"]
    finally:
        controller.close()
        db.close()


def test_restart_requeues_interrupted_job_and_reuses_pinned_snapshot(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.db_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    observed: list[Path] = []

    def runner(**kwargs: Any) -> RunResult:
        observed.append(kwargs["inputs_dir"])
        return _result("run-after-restart")

    first = ReconciliationController(db, settings, run_executor=runner, background=False)
    job, _ = first.create_job(
        session_id="session-b",
        snapshot_path=snapshot,
        snapshot_manifest={"active_sources": {"settlements": {"revision_id": "rev-fixed"}}},
        requested_mode="fake",
        execution_mode="agent",
        provider_id="fake-deterministic-v1",
        policy_fingerprint=FAKE_POLICY_FINGERPRINT,
        simulated=True,
    )
    db.execute(
        "UPDATE reconciliation_jobs SET status = 'RUNNING', attempt_count = 1 WHERE job_id = ?",
        (job["job_id"],),
    )
    first.close()

    restarted = ReconciliationController(db, settings, run_executor=runner, background=False)
    try:
        restarted.start()
        recovered = restarted.get_job(job["job_id"])
        assert recovered is not None
        assert recovered["status"] == "QUEUED"
        assert recovered["failure_code"] == "INTERRUPTED"
        assert recovered["events"][-1]["event_type"] == "RECOVERED"
        completed = restarted.run_once(job["job_id"])
        assert completed["status"] == "SUCCEEDED"
        assert completed["attempt_count"] == 2
        assert observed == [snapshot]
    finally:
        restarted.close()
        db.close()


def test_not_ready_state_is_persisted_as_a_non_runnable_job(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.db_path)
    controller = ReconciliationController(db, settings, background=False)
    try:
        job, reused = controller.create_blocked_job(
            session_id="session-pending",
            snapshot_manifest={"lifecycle_state": "AWAITING_BANK_EVIDENCE"},
            requested_mode="fake",
            provider_id="fake-deterministic-v1",
            policy_fingerprint=FAKE_POLICY_FINGERPRINT,
            reason="missing: bank statement, merchant ledger",
        )
        assert reused is False
        assert job["status"] == "BLOCKED"
        assert job["failure_code"] == "INPUT_NOT_READY"
        assert job["run_id"] is None
        assert job["events"][0]["event_type"] == "INPUT_BLOCKED"
        assert job["progress"]["headline"] == "Waiting for complete evidence"
        assert job["recovery"]["action"] == "COMPLETE_INPUTS"
        repeated, repeated_reused = controller.create_blocked_job(
            session_id="session-pending",
            snapshot_manifest={"lifecycle_state": "AWAITING_BANK_EVIDENCE"},
            requested_mode="fake",
            provider_id="fake-deterministic-v1",
            policy_fingerprint=FAKE_POLICY_FINGERPRINT,
            reason="missing: bank statement, merchant ledger",
        )
        assert repeated_reused is True
        assert repeated["job_id"] == job["job_id"]
    finally:
        controller.close()
        db.close()


def test_failure_is_durable_retryable_and_does_not_persist_exception_text(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, workflow_max_attempts=2)
    db = Database(settings.db_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    def failing(**kwargs: Any) -> RunResult:
        raise RuntimeError("gsk_secret_must_not_be_stored")

    controller = ReconciliationController(db, settings, run_executor=failing, background=False)
    try:
        job, _ = controller.create_job(
            session_id="session-c",
            snapshot_path=snapshot,
            snapshot_manifest={"active_sources": {}},
            requested_mode="fake",
            execution_mode="agent",
            provider_id="fake-deterministic-v1",
            policy_fingerprint=FAKE_POLICY_FINGERPRINT,
            simulated=True,
        )
        failed = controller.run_once(job["job_id"])
        assert failed["status"] == "FAILED"
        assert failed["failure_code"] == "RUN_FAILED"
        assert "gsk_secret" not in str(failed)
        assert failed["recovery"] == {
            "retryable": True,
            "remaining_attempts": 1,
            "action": "RETRY",
        }

        queued = controller.retry(job["job_id"])
        assert queued["status"] == "QUEUED"
        failed_again = controller.run_once(job["job_id"])
        assert failed_again["attempt_count"] == 2
        try:
            controller.retry(job["job_id"])
        except ValueError as exc:
            assert "retry limit" in str(exc)
        else:
            raise AssertionError("retry beyond max_attempts should fail")
    finally:
        controller.close()
        db.close()


def test_failed_progress_identifies_the_exact_stage_without_time_estimates(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, workflow_max_attempts=2)
    db = Database(settings.db_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    def failing(**kwargs: Any) -> RunResult:
        kwargs["progress_callback"]("INPUT_VALIDATION")
        kwargs["progress_callback"]("DETERMINISTIC_RECONCILIATION")
        raise RuntimeError("sensitive implementation detail")

    controller = ReconciliationController(db, settings, run_executor=failing, background=False)
    try:
        job, _ = controller.create_job(
            session_id="session-progress",
            snapshot_path=snapshot,
            snapshot_manifest={"active_sources": {}},
            requested_mode="fake",
            execution_mode="agent",
            provider_id="fake-deterministic-v1",
            policy_fingerprint=FAKE_POLICY_FINGERPRINT,
            simulated=True,
        )
        failed = controller.run_once(job["job_id"])
        assert failed["progress"]["completed_steps"] == 1
        assert failed["progress"]["total_steps"] == 5
        assert [step["state"] for step in failed["progress"]["steps"]] == [
            "COMPLETE",
            "FAILED",
            "PENDING",
            "PENDING",
            "PENDING",
        ]
        assert "percent" not in failed["progress"]
        assert "sensitive implementation detail" not in str(failed)
    finally:
        controller.close()
        db.close()


def test_changed_policy_failure_requires_a_new_request_instead_of_retry(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, workflow_max_attempts=2)
    db = Database(settings.db_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    controller = ReconciliationController(db, settings, background=False)
    try:
        job, _ = controller.create_job(
            session_id="session-stale-policy",
            snapshot_path=snapshot,
            snapshot_manifest={"active_sources": {}},
            requested_mode="fake",
            execution_mode="agent",
            provider_id="fake-deterministic-v1",
            policy_fingerprint="obsolete-policy",
            simulated=True,
        )
        failed = controller.run_once(job["job_id"])
        assert failed["failure_code"] == "PROVIDER_UNAVAILABLE"
        assert failed["recovery"] == {
            "retryable": False,
            "remaining_attempts": 1,
            "action": "START_NEW_REQUEST",
        }
        try:
            controller.retry(job["job_id"])
        except ValueError as exc:
            assert "not retryable" in str(exc)
        else:
            raise AssertionError("a policy-identity failure must not retry the stale job")
    finally:
        controller.close()
        db.close()


def _stage_dev_inputs(settings: Settings, session_id: str) -> None:
    root = Path(__file__).resolve().parents[3]
    session_dir = resolve_session_dir(settings, session_id, create=True)
    for source in ("payments", "refunds", "settlements", "bank_entries", "ledger_entries"):
        content = (root / "datasets" / "dev" / "inputs" / f"{source}.csv").read_text(
            encoding="utf-8"
        )
        stage_source_revision(
            session_dir=session_dir,
            source_type=source,  # type: ignore[arg-type]
            original_filename=f"{source}.csv",
            raw_content=content,
            canonical_csv=content,
            accepted_count=max(len(content.splitlines()) - 1, 0),
            quarantined_count=0,
            origin="MANUAL_CSV",
        )


def test_job_route_returns_quickly_polls_and_deduplicates(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _stage_dev_inputs(settings, "route-session")
    with TestClient(create_app(settings)) as client:
        payload = {"session_id": "route-session", "mode": "fake"}
        started = client.post("/api/v1/ingest/reconciliation-jobs", json=payload)
        assert started.status_code == 202, started.text
        job_id = started.json()["job_id"]

        duplicate = client.post("/api/v1/ingest/reconciliation-jobs", json=payload)
        assert duplicate.status_code == 202
        assert duplicate.json()["job_id"] == job_id
        assert duplicate.json()["reused"] is True

        job = started.json()
        for _ in range(100):
            job = client.get(f"/api/v1/ingest/reconciliation-jobs/{job_id}").json()
            if job["terminal"]:
                break
            time.sleep(0.02)
        assert job["status"] == "SUCCEEDED", job
        assert job["run_id"].startswith("run-")
        assert job["provider_id"] == "fake-deterministic-v1"
        assert job["simulated"] is True
        assert job["summary"]["provider_id"] == "fake-deterministic-v1"
        assert job["progress"]["completed_steps"] == job["progress"]["total_steps"]
        assert job["recovery"]["action"] == "OPEN_RUN"


def test_job_route_does_not_silently_fake_missing_live_provider(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "no-live.sqlite3",
        import_staging_root=tmp_path / "imports",
        ai_provider="auto",
        _env_file=None,
    )
    _stage_dev_inputs(settings, "no-live-session")
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/ingest/reconciliation-jobs",
            json={"session_id": "no-live-session", "mode": "agent"},
        )
        assert response.status_code == 503
        assert "no live" in response.json()["detail"]
        assert client.get("/api/v1/ai/status").json()["investigator"] == "unavailable"
