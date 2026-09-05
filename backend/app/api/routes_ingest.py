"""Multi-Source CSV Ingest & Validation API for ARGUS CONTROL."""

from __future__ import annotations

import csv
import io
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.ai.selection import InvestigatorUnavailableError, resolve_investigator
from app.config import Settings
from app.importers.adapters import REFUND_SPEC
from app.importers.csv_intake import commit_csv_evidence
from app.importers.intake_activation import recover_session_activation
from app.importers.intake_workflow import (
    get_session_status,
    not_ready_detail,
    session_readiness,
    start_session_job,
)
from app.importers.schema_mapping import (
    DocumentType,
    analyze_csv,
)
from app.importers.session_staging import (
    SourceRevisionError,
    resolve_session_dir,
    session_lock,
    snapshot_active_sources,
)
from app.persistence.database import Database
from app.runs import execute_run
from app.workflow.controller import ReconciliationController

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


class AnalyzeCsvPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content: str
    file_type: DocumentType


class MappingSelection(BaseModel):
    target_field: str
    source_column: str


class CommitCsvPayload(AnalyzeCsvPayload):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    mappings: list[MappingSelection]


def _groq_key(settings: Settings) -> str | None:
    return settings.groq_api_key.get_secret_value() if settings.groq_api_key else None


@router.post("/analyze-csv")
def analyze_csv_file(payload: AnalyzeCsvPayload, request: Request) -> dict[str, Any]:
    """Profile a CSV and propose a bounded, reviewable header mapping."""
    if not payload.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=415,
            detail=(
                "This cornerstone accepts CSV files only. Images, OCR, XLSX and PDF are disabled."
            ),
        )
    settings: Settings = request.app.state.settings
    try:
        result = analyze_csv(
            content=payload.content,
            document_type=payload.file_type,
            groq_api_key=_groq_key(settings),
            groq_model=settings.groq_schema_model,
            groq_base_url=settings.groq_base_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["filename"] = payload.filename
    result["groq_configured"] = bool(_groq_key(settings))
    return result


@router.post("/commit-csv")
def commit_csv_file(payload: CommitCsvPayload, request: Request) -> dict[str, Any]:
    """Apply a reviewed mapping and stage every row without model-written values."""
    mapping = {item.target_field: item.source_column for item in payload.mappings}
    if len(mapping) != len(payload.mappings):
        raise HTTPException(status_code=400, detail="A target field was mapped more than once.")
    settings: Settings = request.app.state.settings
    db: Database = request.app.state.db
    try:
        return commit_csv_evidence(
            settings=settings,
            database=db,
            filename=payload.filename,
            content=payload.content,
            file_type=payload.file_type,
            session_id=payload.session_id,
            mapping=mapping,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/status")
def get_ingest_session_status(session_id: str, request: Request) -> dict[str, Any]:
    if not re.fullmatch(SESSION_ID_PATTERN, session_id):
        raise HTTPException(status_code=422, detail="Invalid import session identifier.")
    try:
        return get_session_status(request.app.state.settings, request.app.state.db, session_id)
    except SourceRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/upload-csv", status_code=409)
def legacy_upload_csv_disabled() -> None:
    raise HTTPException(
        status_code=409,
        detail="Direct upload is disabled. Analyze the CSV, review its mapping, then commit it.",
    )


class ReconcileSessionPayload(BaseModel):
    session_id: str = Field(default="default_session", pattern=SESSION_ID_PATTERN)
    fallback_profile: str = "dev"
    mode: str = Field(
        default="agent",
        description="Reconciliation mode (rules-only, agent, or ai-assisted)",
    )


class StartReconciliationJobPayload(BaseModel):
    session_id: str = Field(default="default_session", pattern=SESSION_ID_PATTERN)
    mode: Literal["rules-only", "agent", "fake"] = "agent"


@router.post("/reconciliation-jobs", status_code=202)
def start_reconciliation_job(
    payload: StartReconciliationJobPayload,
    request: Request,
) -> dict[str, Any]:
    """Pin the active evidence and return a durable, pollable workflow job."""
    try:
        return start_session_job(
            settings=request.app.state.settings,
            database=request.app.state.db,
            controller=request.app.state.reconciliation_controller,
            session_id=payload.session_id,
            mode=payload.mode,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvestigatorUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SourceRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/reconciliation-jobs/{job_id}")
def get_reconciliation_job(job_id: str, request: Request) -> dict[str, Any]:
    controller: ReconciliationController = request.app.state.reconciliation_controller
    job = controller.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Reconciliation job was not found.")
    return job


@router.post("/reconciliation-jobs/{job_id}/retry", status_code=202)
def retry_reconciliation_job(job_id: str, request: Request) -> dict[str, Any]:
    controller: ReconciliationController = request.app.state.reconciliation_controller
    try:
        return controller.retry(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Reconciliation job was not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/reconcile-session")
def reconcile_uploaded_session(
    payload: ReconcileSessionPayload,
    request: Request,
) -> dict[str, Any]:
    """Execute deterministic reconciliation on uploaded session files."""
    db: Database = request.app.state.db
    settings: Settings = request.app.state.settings
    session_dir = resolve_session_dir(settings, payload.session_id, create=False)

    if not session_dir or not session_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=(
                f"No uploaded files found for session {payload.session_id!r}. "
                "Please upload files first."
            ),
        )

    try:
        with session_lock(session_dir):
            recover_session_activation(db, session_dir)
            readiness = session_readiness(session_dir)
            inputs_snapshot = None
            if readiness["ready"]:
                refunds_buffer = io.StringIO(newline="")
                csv.writer(refunds_buffer).writerow(REFUND_SPEC.columns)
                inputs_snapshot = snapshot_active_sources(
                    session_dir, empty_refunds=refunds_buffer.getvalue()
                )
    except SourceRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not readiness["ready"]:
        raise HTTPException(
            status_code=409,
            detail=not_ready_detail(readiness),
        )

    exec_mode: Literal["rules-only", "agent"] = (
        "agent" if payload.mode.lower() in ("agent", "ai-assisted") else "rules-only"
    )
    try:
        selection = resolve_investigator(settings, "agent" if exec_mode == "agent" else "none")
    except InvestigatorUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    provider = selection.provider

    try:
        assert inputs_snapshot is not None
        res = execute_run(
            inputs_dir=inputs_snapshot,
            database=db,
            mode=exec_mode,
            provider=provider,
            force=False,
        )
        return {
            "run_id": res.run_id,
            "status": res.status.value,
            "reused": res.reused,
            "economic_output_hash": res.economic_output_hash,
            "summary": res.summary,
            "session_id": payload.session_id,
        }
    except Exception as exc:
        # This compatibility endpoint must not reflect provider responses,
        # record prose, local paths, or secret-bearing exception text.
        raise HTTPException(
            status_code=500,
            detail="Reconciliation did not complete. Use the durable workflow endpoint for status.",
        ) from exc
