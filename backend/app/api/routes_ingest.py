"""Multi-Source CSV Ingest & Validation API for ARGUS CONTROL."""

from __future__ import annotations

import csv
import hashlib
import io
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.persistence.database import Database
from app.runs import execute_run

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

FILE_TYPE_MAP = {
    "payments": "payments.csv",
    "refunds": "refunds.csv",
    "settlements": "settlements.csv",
    "bank_entries": "bank_entries.csv",
    "bank_statements": "bank_entries.csv",
    "bank": "bank_entries.csv",
    "ledger_entries": "ledger_entries.csv",
    "ledger": "ledger_entries.csv",
}

SESSION_DIRS: dict[str, Path] = {}


def _get_or_create_session_dir(session_id: str) -> Path:
    if session_id in SESSION_DIRS and SESSION_DIRS[session_id].is_dir():
        return SESSION_DIRS[session_id]

    temp_dir = Path(tempfile.mkdtemp(prefix=f"argus_upload_{session_id}_"))
    SESSION_DIRS[session_id] = temp_dir
    return temp_dir


class UploadCsvPayload(BaseModel):
    filename: str = Field(description="Name of the uploaded CSV file")
    content: str = Field(description="Raw CSV text content")
    file_type: str = Field(
        default="auto", description="Target file category (payments, settlements, bank, ledger)"
    )
    session_id: str = Field(default="default_session", description="Session identifier")


@router.post("/upload-csv")
def upload_csv_file(payload: UploadCsvPayload) -> dict[str, Any]:
    """Upload and validate a single CSV file (payments, refunds, settlements, bank, or ledger)."""
    text_content = payload.content.strip()
    if not text_content:
        raise HTTPException(status_code=400, detail="Uploaded CSV content is empty.")

    raw_bytes = text_content.encode("utf-8")
    sha256_hash = hashlib.sha256(raw_bytes).hexdigest()

    # Parse and validate CSV
    reader = csv.DictReader(io.StringIO(text_content))
    fieldnames = reader.fieldnames or []
    if not fieldnames:
        raise HTTPException(status_code=400, detail="CSV contains no header columns.")

    rows: list[dict[str, str]] = []
    for idx, row in enumerate(reader):
        if idx >= 5:  # Store first 5 rows for UI preview
            break
        rows.append(dict(row))

    total_rows = text_content.count("\n")

    # Determine target filename & type
    target_filename = payload.filename or "uploaded.csv"
    norm_type = payload.file_type.lower().strip()

    if norm_type in FILE_TYPE_MAP:
        target_filename = FILE_TYPE_MAP[norm_type]
    elif "payment" in payload.filename.lower():
        target_filename = "payments.csv"
        norm_type = "payments"
    elif "refund" in payload.filename.lower():
        target_filename = "refunds.csv"
        norm_type = "refunds"
    elif "settle" in payload.filename.lower():
        target_filename = "settlements.csv"
        norm_type = "settlements"
    elif "bank" in payload.filename.lower():
        target_filename = "bank_entries.csv"
        norm_type = "bank_entries"
    elif "ledger" in payload.filename.lower():
        target_filename = "ledger_entries.csv"
        norm_type = "ledger_entries"
    else:
        target_filename = "payments.csv"
        norm_type = "payments"

    # Canonicalize CSV format to strict AdapterSpec invariants
    from app.importers.document_extractor import canonicalize_csv_text

    canonical_csv = canonicalize_csv_text(text_content, norm_type)
    if not canonical_csv.strip():
        raise HTTPException(
            status_code=400,
            detail=(
                f"The CSV file {payload.filename!r} does not contain recognizable financial "
                "transaction headers (e.g. Amount, Date, UTR, Reference ID, Fee, Tax)."
            ),
        )

    # Save to session input directory
    session_dir = _get_or_create_session_dir(payload.session_id)
    dest_path = session_dir / target_filename
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(canonical_csv)

    canonical_bytes = canonical_csv.encode("utf-8")
    sha256_hash = hashlib.sha256(canonical_bytes).hexdigest()

    return {
        "filename": payload.filename,
        "mapped_filename": target_filename,
        "file_type": norm_type,
        "rows_count": total_rows,
        "headers": fieldnames,
        "checksum_sha256": sha256_hash,
        "preview_rows": rows,
        "session_id": payload.session_id,
        "status": "VALIDATED",
    }


class UploadDocumentPayload(BaseModel):
    filename: str = Field(description="Name of the uploaded PDF or image file")
    content_base64: str = Field(description="Base64 encoded file content")
    mime_type: str = Field(default="application/pdf", description="MIME type of document")
    session_id: str = Field(default="default_session", description="Session identifier")


@router.post("/upload-document")
def upload_document_file(payload: UploadDocumentPayload) -> dict[str, Any]:
    """Extract tabular financial data from an uploaded PDF or image using Vision / OCR."""
    from app.importers.document_extractor import (
        convert_extracted_records_to_csv,
        extract_financial_data_from_document,
    )

    if not payload.content_base64.strip():
        raise HTTPException(status_code=400, detail="Document content is empty.")

    extracted = extract_financial_data_from_document(
        filename=payload.filename,
        content_base64=payload.content_base64,
        mime_type=payload.mime_type,
    )

    records = extracted.get("records", [])
    if not extracted.get("is_financial", True) or not records:
        err_msg = extracted.get(
            "error",
            (
                f"The file {payload.filename!r} is not a recognized banking or payment record. "
                "No financial transaction tables detected."
            ),
        )
        raise HTTPException(status_code=400, detail=err_msg)

    doc_type = extracted.get("document_type", "payments")
    csv_text = convert_extracted_records_to_csv(records, doc_type)

    target_filename = FILE_TYPE_MAP.get(doc_type, "payments.csv")
    session_dir = _get_or_create_session_dir(payload.session_id)
    dest_path = session_dir / target_filename
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(csv_text)

    raw_bytes = csv_text.encode("utf-8")
    sha256_hash = hashlib.sha256(raw_bytes).hexdigest()

    return {
        "filename": payload.filename,
        "mapped_filename": target_filename,
        "file_type": doc_type,
        "rows_count": len(records),
        "checksum_sha256": sha256_hash,
        "preview_rows": records[:5],
        "session_id": payload.session_id,
        "extractor": extracted.get("extractor", "multimodal_vision"),
        "confidence": extracted.get("confidence", 0.99),
        "status": "VALIDATED",
    }


class ReconcileSessionPayload(BaseModel):
    session_id: str = "default_session"
    fallback_profile: str = "dev"
    mode: str = "rules-only"


@router.post("/reconcile-session")
def reconcile_uploaded_session(
    payload: ReconcileSessionPayload,
    request: Request,
) -> dict[str, Any]:
    """Execute deterministic reconciliation on uploaded session files."""
    db: Database = request.app.state.db
    session_dir = SESSION_DIRS.get(payload.session_id)

    if not session_dir or not session_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=(
                f"No uploaded files found for session {payload.session_id!r}. "
                "Please upload files first."
            ),
        )

    # For any missing standard input files, copy from fallback profile
    repo_root = Path(__file__).resolve().parents[3]
    fallback_dir = repo_root / "datasets" / payload.fallback_profile / "inputs"

    for standard_file in [
        "payments.csv",
        "refunds.csv",
        "settlements.csv",
        "bank_entries.csv",
        "ledger_entries.csv",
    ]:
        target = session_dir / standard_file
        if not target.exists() and (fallback_dir / standard_file).exists():
            shutil.copyfile(fallback_dir / standard_file, target)

    try:
        res = execute_run(
            inputs_dir=session_dir,
            database=db,
            mode=payload.mode,  # type: ignore
            force=True,
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
        raise HTTPException(status_code=500, detail=str(exc)) from exc
