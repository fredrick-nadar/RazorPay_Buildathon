"""Multi-Source CSV Ingest & Validation API for ARGUS CONTROL."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.ai.selection import InvestigatorUnavailableError, resolve_investigator
from app.config import Settings
from app.importers.adapters import (
    BANK_SPEC,
    LEDGER_SPEC,
    PAYMENT_SPEC,
    REFUND_SPEC,
    SETTLEMENT_SPEC,
    QuarantineSignal,
    parse_bank_row,
    parse_ledger_row,
    parse_payment_row,
    parse_refund_row,
    parse_settlement_row,
)
from app.importers.intake_activation import recover_session_activation
from app.importers.schema_mapping import (
    DocumentType,
    analyze_csv,
    canonicalize_with_mapping,
)
from app.importers.session_staging import (
    CANONICAL_FILENAMES,
    SourceRevisionError,
    materialize_active_sources,
    resolve_session_dir,
    session_lock,
    session_source_status,
    snapshot_active_sources,
    stage_source_revision,
    verified_active_sources,
)
from app.persistence.database import Database
from app.runs import execute_run
from app.workflow.controller import ReconciliationController

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


def get_or_create_session_dir(session_id: str, settings: Settings) -> Path:
    """Public staging boundary shared by manual and Razorpay Test Mode imports."""
    return resolve_session_dir(settings, session_id, create=True)


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


_PARSER_BY_TYPE = {
    "payments": (PAYMENT_SPEC, parse_payment_row),
    "refunds": (REFUND_SPEC, parse_refund_row),
    "settlements": (SETTLEMENT_SPEC, parse_settlement_row),
    "bank_entries": (BANK_SPEC, parse_bank_row),
    "ledger_entries": (LEDGER_SPEC, parse_ledger_row),
}


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


def validate_canonical_rows(
    canonical_csv: str, file_type: DocumentType
) -> tuple[int, int, list[dict[str, Any]]]:
    spec, parser = _PARSER_BY_TYPE[file_type]
    reader = csv.DictReader(io.StringIO(canonical_csv))
    accepted = 0
    quarantined: list[dict[str, Any]] = []
    for row_number, row in enumerate(reader, start=1):
        canonical = {column: str(row.get(column) or "") for column in spec.columns}
        try:
            parser(canonical, row_number, f"staged/{spec.file_stem}.csv")
            accepted += 1
        except QuarantineSignal as exc:
            quarantined.append(
                {
                    "row_number": row_number,
                    "reason": exc.reason.value,
                    "detail": exc.detail,
                }
            )
    return accepted, len(quarantined), quarantined[:20]


@router.post("/commit-csv")
def commit_csv_file(payload: CommitCsvPayload, request: Request) -> dict[str, Any]:
    """Apply a reviewed mapping and stage every row without model-written values."""
    mapping = {item.target_field: item.source_column for item in payload.mappings}
    if len(mapping) != len(payload.mappings):
        raise HTTPException(status_code=400, detail="A target field was mapped more than once.")
    try:
        canonical_csv, profile = canonicalize_with_mapping(
            content=payload.content,
            document_type=payload.file_type,
            mapping=mapping,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    accepted, quarantined, quarantine_preview = validate_canonical_rows(
        canonical_csv, payload.file_type
    )
    settings: Settings = request.app.state.settings
    db: Database = request.app.state.db
    session_dir = get_or_create_session_dir(payload.session_id, settings)
    try:
        with session_lock(session_dir):
            recover_session_activation(db, session_dir)
            activation = stage_source_revision(
                session_dir=session_dir,
                source_type=payload.file_type,
                original_filename=payload.filename,
                raw_content=payload.content,
                canonical_csv=canonical_csv,
                accepted_count=accepted,
                quarantined_count=quarantined,
                origin="MANUAL_CSV",
                mapping=mapping,
            )
    except SourceRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    preview_reader = csv.DictReader(io.StringIO(canonical_csv))
    preview_rows = [dict(row) for index, row in enumerate(preview_reader) if index < 5]
    return {
        "filename": payload.filename,
        "mapped_filename": activation.canonical_filename,
        "file_type": payload.file_type,
        "rows_count": len(profile.rows),
        "session_rows_count": len(profile.rows),
        "accepted_count": accepted,
        "quarantined_count": quarantined,
        "quarantine_preview": quarantine_preview,
        "checksum_sha256": profile.sha256,
        "preview_rows": preview_rows,
        "session_id": payload.session_id,
        "status": "READY" if quarantined == 0 else "READY_WITH_WARNINGS",
        "reused": activation.reused,
        "revision_id": activation.revision_id,
        "revision_number": activation.revision_number,
        "replaced_revision_id": activation.replaced_revision_id,
        "active": True,
    }


_REQUIRED_SOURCE_LABELS = {
    "payments": "Razorpay payments",
    "settlements": "Razorpay settlements",
    "bank_entries": "bank statement",
    "ledger_entries": "merchant ledger",
}


def _session_readiness(session_dir: Path) -> dict[str, Any]:
    with session_lock(session_dir):
        status = session_source_status(session_dir)
        if status["active_sources"]:
            verified_active_sources(session_dir, require_demo_metadata=False)
            materialize_active_sources(session_dir)
    active = status["active_sources"]
    missing = [label for source, label in _REQUIRED_SOURCE_LABELS.items() if source not in active]
    empty = [
        label
        for source, label in _REQUIRED_SOURCE_LABELS.items()
        if source in active and int(active[source]["accepted_count"]) == 0
    ]
    gateway_ready = all(
        source in active and int(active[source]["accepted_count"]) > 0
        for source in ("payments", "settlements")
    )
    # Legacy full-demo bundles generated both merchant sides from gateway data.
    # Keep those immutable revisions for audit, but require independent uploads
    # before this intake workflow can reconcile. Uploaded synthetic fixtures are
    # valid demo inputs; automatically manufactured receipt/accounting is not.
    merchant_upload_required = [
        source
        for source in ("bank_entries", "ledger_entries")
        if source in active and active[source]["origin"] == "SYNTHETIC_DEMO"
    ]
    bank_ready = (
        "bank_entries" in active
        and int(active["bank_entries"]["accepted_count"]) > 0
        and "bank_entries" not in merchant_upload_required
    )
    ledger_ready = (
        "ledger_entries" in active
        and int(active["ledger_entries"]["accepted_count"]) > 0
        and "ledger_entries" not in merchant_upload_required
    )
    payments_available = "payments" in active and int(active["payments"]["accepted_count"]) > 0
    if gateway_ready and bank_ready and ledger_ready:
        lifecycle_state = "READY_TO_RECONCILE"
    elif gateway_ready and not bank_ready:
        lifecycle_state = "AWAITING_BANK_EVIDENCE"
    elif gateway_ready and not ledger_ready:
        lifecycle_state = "AWAITING_LEDGER_EVIDENCE"
    elif payments_available:
        lifecycle_state = "AWAITING_RAZORPAY_SETTLEMENT"
    else:
        lifecycle_state = "GATEWAY_IMPORT_REQUIRED"
    return {
        **status,
        "ready": gateway_ready and bank_ready and ledger_ready,
        "gateway_ready": gateway_ready,
        "bank_ready": bank_ready,
        "ledger_ready": ledger_ready,
        "ready_source_groups": sum((gateway_ready, bank_ready, ledger_ready)),
        "missing_sources": missing,
        "empty_sources": empty,
        "merchant_upload_required": merchant_upload_required,
        "settlement_reconciliation_required": (
            "settlements" not in active or int(active["settlements"]["accepted_count"]) == 0
        ),
        "lifecycle_state": lifecycle_state,
        "gateway_import_id": (
            active.get("payments", {}).get("external_import_id")
            if isinstance(active.get("payments"), dict)
            else None
        ),
    }


@router.get("/sessions/{session_id}/status")
def get_ingest_session_status(session_id: str, request: Request) -> dict[str, Any]:
    if not re.fullmatch(SESSION_ID_PATTERN, session_id):
        raise HTTPException(status_code=422, detail="Invalid import session identifier.")
    settings: Settings = request.app.state.settings
    session_dir = resolve_session_dir(settings, session_id, create=False)
    if not session_dir.is_dir():
        return {
            "session_id": session_id,
            "ready": False,
            "gateway_ready": False,
            "bank_ready": False,
            "ledger_ready": False,
            "ready_source_groups": 0,
            "missing_sources": list(_REQUIRED_SOURCE_LABELS.values()),
            "empty_sources": [],
            "merchant_upload_required": [],
            "settlement_reconciliation_required": True,
            "lifecycle_state": "GATEWAY_IMPORT_REQUIRED",
            "gateway_import_id": None,
            "active_sources": {},
            "revision_counts": {source: 0 for source in CANONICAL_FILENAMES},
        }
    try:
        recover_session_activation(request.app.state.db, session_dir)
        return {"session_id": session_id, **_session_readiness(session_dir)}
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


def _not_ready_detail(readiness: dict[str, Any]) -> str:
    details: list[str] = []
    if readiness["missing_sources"]:
        details.append("missing: " + ", ".join(readiness["missing_sources"]))
    if readiness["empty_sources"]:
        details.append("no eligible rows: " + ", ".join(readiness["empty_sources"]))
    if readiness["merchant_upload_required"]:
        details.append(
            "separate merchant upload required: "
            + ", ".join(
                _REQUIRED_SOURCE_LABELS[source] for source in readiness["merchant_upload_required"]
            )
        )
    return (
        "Full reconciliation is not ready (" + "; ".join(details) + "). "
        "Imported sources remain available, but ARGUS will not create a complete run "
        "until Razorpay, bank, and ledger evidence are present."
    )


@router.post("/reconciliation-jobs", status_code=202)
def start_reconciliation_job(
    payload: StartReconciliationJobPayload,
    request: Request,
) -> dict[str, Any]:
    """Pin the active evidence and return a durable, pollable workflow job."""
    settings: Settings = request.app.state.settings
    controller: ReconciliationController = request.app.state.reconciliation_controller
    session_dir = resolve_session_dir(settings, payload.session_id, create=False)
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="No import session exists for this identifier.")

    selection_request = "none" if payload.mode == "rules-only" else payload.mode
    try:
        selection = resolve_investigator(settings, selection_request)
    except InvestigatorUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        with session_lock(session_dir):
            recover_session_activation(request.app.state.db, session_dir)
            readiness = _session_readiness(session_dir)
            manifest = {
                "lifecycle_state": readiness["lifecycle_state"],
                "active_sources": readiness["active_sources"],
            }
            if not readiness["ready"]:
                job, reused = controller.create_blocked_job(
                    session_id=payload.session_id,
                    snapshot_manifest=manifest,
                    requested_mode=selection_request,
                    provider_id=selection.provider_id,
                    reason=_not_ready_detail(readiness),
                    policy_fingerprint=selection.policy_fingerprint,
                )
                return {**job, "reused": reused}
            refunds_buffer = io.StringIO(newline="")
            csv.writer(refunds_buffer).writerow(REFUND_SPEC.columns)
            snapshot = snapshot_active_sources(session_dir, empty_refunds=refunds_buffer.getvalue())
    except SourceRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    job, reused = controller.create_job(
        session_id=payload.session_id,
        snapshot_path=snapshot,
        snapshot_manifest=manifest,
        requested_mode=selection_request,
        execution_mode=selection.execution_mode,
        provider_id=selection.provider_id,
        simulated=selection.simulated,
        policy_fingerprint=selection.policy_fingerprint,
    )
    if job["status"] == "QUEUED":
        controller.enqueue(job["job_id"])
    return {**job, "reused": reused}


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
            readiness = _session_readiness(session_dir)
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
            detail=_not_ready_detail(readiness),
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
