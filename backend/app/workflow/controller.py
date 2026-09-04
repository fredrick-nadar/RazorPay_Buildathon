"""SQLite-backed single-worker controller for reconciliation runs.

The job row is durable; the worker thread is deliberately replaceable.  On a
process restart, interrupted jobs return to the queue and replay the immutable
input snapshot. ``execute_run`` supplies the financial idempotency boundary, so
recovery cannot create a second economic result.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ai.selection import InvestigatorUnavailableError, resolve_investigator
from app.config import Settings
from app.persistence.database import Database
from app.runs import RunResult, execute_run

RunExecutor = Callable[..., RunResult]
TERMINAL_STATUSES = frozenset({"BLOCKED", "SUCCEEDED", "FAILED"})


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _job_id(request_key: str) -> str:
    return f"job-{request_key[:20]}"


def request_key_for(
    *,
    session_id: str,
    snapshot_identity: str,
    requested_mode: str,
    provider_id: str,
    policy_fingerprint: str = "policy-unversioned",
) -> str:
    """Identity of one reconciliation request.

    The execution-policy fingerprint participates so a materially changed
    investigator policy (models, deadlines, retries, tool budget, prompt/tool
    protocol) produces a NEW job instead of reusing one that timed out under
    the old policy. Identical submissions under the same policy stay idempotent.
    """
    material = json.dumps(
        {
            "version": "reconciliation-job-v2",
            "session_id": session_id,
            "snapshot_identity": snapshot_identity,
            "requested_mode": requested_mode,
            "provider_id": provider_id,
            "policy_fingerprint": policy_fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _safe_failure(exc: Exception) -> tuple[str, str]:
    """Return bounded diagnostics without response bodies, prompts, or secrets."""
    if isinstance(exc, InvestigatorUnavailableError):
        return "PROVIDER_UNAVAILABLE", str(exc)[:500]
    name = type(exc).__name__
    if name in {"LLMError", "AIChainError", "TimeoutError"}:
        return "PROVIDER_FAILURE", f"{name}: live investigator did not complete"
    return "RUN_FAILED", f"{name}: reconciliation did not complete"[:500]


class ReconciliationController:
    """One persisted queue drained by one in-process worker."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        run_executor: RunExecutor = execute_run,
        background: bool = True,
    ) -> None:
        self.database = database
        self.settings = settings
        self.run_executor = run_executor
        self.background = background
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="argus-reconciliation"
        )
        self._lock = threading.Lock()
        self._scheduled: set[str] = set()
        self._closed = False

    def start(self) -> None:
        """Recover work left RUNNING by this single-process controller."""
        rows = self.database.query_all(
            "SELECT job_id, attempt_count, max_attempts FROM reconciliation_jobs "
            "WHERE status = 'RUNNING' ORDER BY created_at_utc"
        )
        for row in rows:
            job_id = str(row["job_id"])
            if int(row["attempt_count"]) >= int(row["max_attempts"]):
                self._transition(
                    job_id,
                    "FAILED",
                    event_type="RECOVERY_EXHAUSTED",
                    failure_code="INTERRUPTED",
                    failure_detail="The prior process stopped and the retry limit was reached.",
                    finished=True,
                )
            else:
                self._transition(
                    job_id,
                    "QUEUED",
                    event_type="RECOVERED",
                    failure_code="INTERRUPTED",
                    failure_detail=(
                        "The prior process stopped; the immutable snapshot was re-queued."
                    ),
                )
        if self.background:
            for row in self.database.query_all(
                "SELECT job_id FROM reconciliation_jobs WHERE status = 'QUEUED' "
                "ORDER BY created_at_utc"
            ):
                self.enqueue(str(row["job_id"]))

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=False)

    def create_job(
        self,
        *,
        session_id: str,
        snapshot_path: Path,
        snapshot_manifest: dict[str, Any],
        requested_mode: str,
        execution_mode: str,
        provider_id: str,
        simulated: bool,
        policy_fingerprint: str = "policy-unversioned",
    ) -> tuple[dict[str, Any], bool]:
        identity = hashlib.sha256(
            json.dumps(snapshot_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        key = request_key_for(
            session_id=session_id,
            snapshot_identity=identity,
            requested_mode=requested_mode,
            provider_id=provider_id,
            policy_fingerprint=policy_fingerprint,
        )
        job_id = _job_id(key)
        now = _now()
        created = False
        with self.database.transaction(immediate=True):
            existing = self.database.query_one(
                "SELECT job_id FROM reconciliation_jobs WHERE request_key = ?", (key,)
            )
            if existing is not None:
                job_id = str(existing["job_id"])
            else:
                self.database.execute(
                    "INSERT INTO reconciliation_jobs (job_id, request_key, session_id, "
                    "snapshot_path, snapshot_manifest_json, requested_mode, execution_mode, "
                    "provider_id, simulated, policy_fingerprint, status, attempt_count, "
                    "max_attempts, run_id, "
                    "failure_code, failure_detail, created_at_utc, updated_at_utc, "
                    "started_at_utc, finished_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        key,
                        session_id,
                        str(snapshot_path),
                        json.dumps(snapshot_manifest, sort_keys=True),
                        requested_mode,
                        execution_mode,
                        provider_id,
                        1 if simulated else 0,
                        policy_fingerprint,
                        "QUEUED",
                        0,
                        self.settings.workflow_max_attempts,
                        None,
                        None,
                        None,
                        now,
                        now,
                        None,
                        None,
                    ),
                )
                self._append_event(job_id, "CREATED", "QUEUED", {"snapshot_identity": identity})
                created = True
        job = self.get_job(job_id)
        assert job is not None
        return job, not created

    def create_blocked_job(
        self,
        *,
        session_id: str,
        snapshot_manifest: dict[str, Any],
        requested_mode: str,
        provider_id: str,
        reason: str,
        policy_fingerprint: str = "policy-unversioned",
    ) -> tuple[dict[str, Any], bool]:
        identity = hashlib.sha256(
            json.dumps(snapshot_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        key = request_key_for(
            session_id=session_id,
            snapshot_identity=identity,
            requested_mode=requested_mode,
            provider_id=provider_id,
            policy_fingerprint=policy_fingerprint,
        )
        job_id = _job_id(key)
        now = _now()
        created = False
        with self.database.transaction(immediate=True):
            existing = self.database.query_one(
                "SELECT job_id FROM reconciliation_jobs WHERE request_key = ?", (key,)
            )
            if existing is not None:
                job_id = str(existing["job_id"])
            else:
                self.database.execute(
                    "INSERT INTO reconciliation_jobs (job_id, request_key, session_id, "
                    "snapshot_path, snapshot_manifest_json, requested_mode, execution_mode, "
                    "provider_id, simulated, policy_fingerprint, status, attempt_count, "
                    "max_attempts, run_id, "
                    "failure_code, failure_detail, created_at_utc, updated_at_utc, "
                    "started_at_utc, finished_at_utc) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, 0, ?, "
                    "'BLOCKED', 0, ?, NULL, 'INPUT_NOT_READY', ?, ?, ?, NULL, ?)",
                    (
                        job_id,
                        key,
                        session_id,
                        json.dumps(snapshot_manifest, sort_keys=True),
                        requested_mode,
                        "rules-only" if requested_mode == "rules-only" else "agent",
                        provider_id,
                        policy_fingerprint,
                        self.settings.workflow_max_attempts,
                        reason[:500],
                        now,
                        now,
                        now,
                    ),
                )
                self._append_event(job_id, "INPUT_BLOCKED", "BLOCKED", {"reason": reason[:500]})
                created = True
        job = self.get_job(job_id)
        assert job is not None
        return job, not created

    def enqueue(self, job_id: str) -> None:
        if not self.background:
            return
        with self._lock:
            if self._closed or job_id in self._scheduled:
                return
            self._scheduled.add(job_id)
        self._executor.submit(self._run_scheduled, job_id)

    def run_once(self, job_id: str) -> dict[str, Any]:
        """Run one queued job synchronously; primarily useful for deterministic tests."""
        self._execute(job_id)
        job = self.get_job(job_id)
        assert job is not None
        return job

    def retry(self, job_id: str) -> dict[str, Any]:
        row = self.database.query_one(
            "SELECT status, attempt_count, max_attempts FROM reconciliation_jobs WHERE job_id = ?",
            (job_id,),
        )
        if row is None:
            raise KeyError(job_id)
        if str(row["status"]) != "FAILED":
            raise ValueError("only failed reconciliation jobs can be retried")
        if int(row["attempt_count"]) >= int(row["max_attempts"]):
            raise ValueError("reconciliation job retry limit reached")
        self._transition(
            job_id,
            "QUEUED",
            event_type="RETRY_QUEUED",
            failure_code=None,
            failure_detail=None,
            clear_finished=True,
        )
        self.enqueue(job_id)
        job = self.get_job(job_id)
        assert job is not None
        return job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.database.query_one(
            "SELECT * FROM reconciliation_jobs WHERE job_id = ?", (job_id,)
        )
        if row is None:
            return None
        events = self.database.query_all(
            "SELECT sequence, event_type, status, detail_json, created_at_utc "
            "FROM reconciliation_job_events WHERE job_id = ? ORDER BY sequence",
            (job_id,),
        )
        summary: dict[str, Any] | None = None
        if row["run_id"]:
            run = self.database.query_one(
                "SELECT summary_json FROM runs WHERE run_id = ?", (row["run_id"],)
            )
            if run is not None:
                summary = json.loads(str(run["summary_json"]))
        return {
            "job_id": str(row["job_id"]),
            "session_id": str(row["session_id"]),
            "status": str(row["status"]),
            "phase": self._phase_for(str(row["status"]), events),
            "terminal": str(row["status"]) in TERMINAL_STATUSES,
            "requested_mode": str(row["requested_mode"]),
            "execution_mode": str(row["execution_mode"]),
            "provider_id": str(row["provider_id"]),
            "policy_fingerprint": str(row["policy_fingerprint"]),
            "simulated": bool(row["simulated"]),
            "attempt_count": int(row["attempt_count"]),
            "max_attempts": int(row["max_attempts"]),
            "run_id": str(row["run_id"]) if row["run_id"] else None,
            "failure_code": str(row["failure_code"]) if row["failure_code"] else None,
            "failure_detail": str(row["failure_detail"]) if row["failure_detail"] else None,
            "created_at_utc": str(row["created_at_utc"]),
            "updated_at_utc": str(row["updated_at_utc"]),
            "started_at_utc": str(row["started_at_utc"]) if row["started_at_utc"] else None,
            "finished_at_utc": str(row["finished_at_utc"]) if row["finished_at_utc"] else None,
            "summary": summary,
            "events": [
                {
                    "sequence": int(event["sequence"]),
                    "event_type": str(event["event_type"]),
                    "status": str(event["status"]),
                    "detail": json.loads(str(event["detail_json"])),
                    "created_at_utc": str(event["created_at_utc"]),
                }
                for event in events
            ],
        }

    def _run_scheduled(self, job_id: str) -> None:
        try:
            self._execute(job_id)
        finally:
            with self._lock:
                self._scheduled.discard(job_id)

    def _execute(self, job_id: str) -> None:
        row = self.database.query_one(
            "SELECT * FROM reconciliation_jobs WHERE job_id = ?", (job_id,)
        )
        if row is None or str(row["status"]) != "QUEUED":
            return
        now = _now()
        with self.database.transaction(immediate=True):
            current = self.database.query_one(
                "SELECT status FROM reconciliation_jobs WHERE job_id = ?", (job_id,)
            )
            if current is None or str(current["status"]) != "QUEUED":
                return
            self.database.execute(
                "UPDATE reconciliation_jobs SET status = 'RUNNING', "
                "attempt_count = attempt_count + 1, started_at_utc = COALESCE(started_at_utc, ?), "
                "updated_at_utc = ?, failure_code = NULL, failure_detail = NULL WHERE job_id = ?",
                (now, now, job_id),
            )
            self._append_event(job_id, "ATTEMPT_STARTED", "RUNNING", {})

        row = self.database.query_one(
            "SELECT * FROM reconciliation_jobs WHERE job_id = ?", (job_id,)
        )
        assert row is not None
        try:
            requested = str(row["requested_mode"])
            if str(row["execution_mode"]) == "rules-only":
                selection = resolve_investigator(self.settings, "none")
            else:
                selection = resolve_investigator(self.settings, requested)
            if selection.provider_id != str(row["provider_id"]):
                raise InvestigatorUnavailableError(
                    "configured investigator changed after this job was created; start a new job"
                )
            if selection.policy_fingerprint != str(row["policy_fingerprint"]):
                # The execution policy moved under a queued job. Refuse rather
                # than silently running the new policy under the old identity.
                raise InvestigatorUnavailableError(
                    "investigator execution policy changed after this job was "
                    "created; start a new job"
                )
            snapshot_path = Path(str(row["snapshot_path"]))
            if not snapshot_path.is_dir():
                raise FileNotFoundError("immutable reconciliation snapshot is unavailable")
            result = self.run_executor(
                inputs_dir=snapshot_path,
                database=self.database,
                mode=selection.execution_mode,
                provider=selection.provider,
                force=False,
                progress_callback=lambda phase: self._record_progress(job_id, phase),
            )
        except Exception as exc:  # noqa: BLE001 - convert to durable safe failure
            code, detail = _safe_failure(exc)
            self._transition(
                job_id,
                "FAILED",
                event_type="ATTEMPT_FAILED",
                failure_code=code,
                failure_detail=detail,
                finished=True,
            )
            return

        self._transition(
            job_id,
            "SUCCEEDED",
            event_type="RUN_LINKED",
            run_id=result.run_id,
            detail={"reused": result.reused},
            finished=True,
        )

    def _record_progress(self, job_id: str, phase: str) -> None:
        with self.database.transaction(immediate=True):
            self.database.execute(
                "UPDATE reconciliation_jobs SET updated_at_utc = ? WHERE job_id = ?",
                (_now(), job_id),
            )
            self._append_event(job_id, "PROGRESS", "RUNNING", {"phase": phase})

    @staticmethod
    def _phase_for(status: str, events: list[Any]) -> str:
        if status != "RUNNING":
            return status
        for event in reversed(events):
            if str(event["event_type"]) == "PROGRESS":
                try:
                    detail = json.loads(str(event["detail_json"]))
                    if isinstance(detail.get("phase"), str):
                        return str(detail["phase"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    break
        return status

    def _transition(
        self,
        job_id: str,
        status: str,
        *,
        event_type: str,
        failure_code: str | None = None,
        failure_detail: str | None = None,
        run_id: str | None = None,
        detail: dict[str, Any] | None = None,
        finished: bool = False,
        clear_finished: bool = False,
    ) -> None:
        now = _now()
        finished_value = now if finished else None
        with self.database.transaction(immediate=True):
            self.database.execute(
                "UPDATE reconciliation_jobs SET status = ?, failure_code = ?, "
                "failure_detail = ?, run_id = COALESCE(?, run_id), updated_at_utc = ?, "
                "finished_at_utc = CASE WHEN ? THEN NULL WHEN ? THEN ? ELSE finished_at_utc END "
                "WHERE job_id = ?",
                (
                    status,
                    failure_code,
                    failure_detail,
                    run_id,
                    now,
                    1 if clear_finished else 0,
                    1 if finished else 0,
                    finished_value,
                    job_id,
                ),
            )
            self._append_event(job_id, event_type, status, detail or {})

    def _append_event(
        self, job_id: str, event_type: str, status: str, detail: dict[str, Any]
    ) -> None:
        row = self.database.query_one(
            "SELECT COALESCE(MAX(sequence), 0) AS last_sequence "
            "FROM reconciliation_job_events WHERE job_id = ?",
            (job_id,),
        )
        sequence = int(row["last_sequence"]) + 1 if row else 1
        self.database.execute(
            "INSERT INTO reconciliation_job_events (event_id, job_id, sequence, event_type, "
            "status, detail_json, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"wfe-{uuid.uuid4().hex}",
                job_id,
                sequence,
                event_type,
                status,
                json.dumps(detail, sort_keys=True),
                _now(),
            ),
        )


__all__ = [
    "ReconciliationController",
    "TERMINAL_STATUSES",
    "request_key_for",
]
