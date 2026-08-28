"""Multi-Source CSV Ingest & Validation API for ARGUS CONTROL."""

from __future__ import annotations

import csv
import hashlib
import io
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.routes_runs import _resolve_agent_provider
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

PRIMARY_ID_MAP = {
    "payments.csv": "payment_id",
    "refunds.csv": "refund_id",
    "settlements.csv": "settlement_id",
    "bank_entries.csv": "bank_entry_id",
    "ledger_entries.csv": "ledger_entry_id",
}


def _get_or_create_session_dir(session_id: str) -> Path:
    if session_id in SESSION_DIRS and SESSION_DIRS[session_id].is_dir():
        return SESSION_DIRS[session_id]

    temp_dir = Path(tempfile.mkdtemp(prefix=f"argus_upload_{session_id}_"))
    SESSION_DIRS[session_id] = temp_dir
    return temp_dir


def _merge_and_save_csv(
    dest_path: Path,
    new_csv_text: str,
    target_filename: str,
) -> int:
    """Save or append new CSV records with existing records in the session staging dir.

    Enables stacking multiple files (e.g. Live API + manual PDF/CSV uploads) to test
    large data volumes (1,000+ to 5,000+ records) without overwriting or duplicate ID collisions.
    """
    id_field = PRIMARY_ID_MAP.get(target_filename, "payment_id")
    cleaned_new = new_csv_text.strip()
    if not cleaned_new:
        return 0

    if not dest_path.exists() or dest_path.stat().st_size < 10:
        with open(dest_path, "w", newline="", encoding="utf-8") as f:
            f.write(cleaned_new + "\n")
        return max(0, cleaned_new.count("\n"))

    # Read existing rows
    existing_rows: list[dict[str, str]] = []
    fieldnames: list[str] = []
    with open(dest_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for r in reader:
            existing_rows.append(dict(r))

    # Read incoming new rows
    new_reader = csv.DictReader(io.StringIO(cleaned_new))
    new_fieldnames = list(new_reader.fieldnames or [])
    if not fieldnames:
        fieldnames = new_fieldnames
    else:
        # Merge missing headers if any
        for h in new_fieldnames:
            if h not in fieldnames:
                fieldnames.append(h)

    seen_ids = {r.get(id_field) for r in existing_rows if r.get(id_field)}

    for r in new_reader:
        orig_id = r.get(id_field)
        if orig_id and orig_id in seen_ids:
            # Suffix colliding IDs so multi-file uploads stack up smoothly without quarantine
            counter = 2
            while f"{orig_id}_imp{counter}" in seen_ids:
                counter += 1
            new_id = f"{orig_id}_imp{counter}"
            r[id_field] = new_id
            seen_ids.add(new_id)
        elif orig_id:
            seen_ids.add(orig_id)
        existing_rows.append(dict(r))

    # Write merged file back
    with open(dest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)

    return len(existing_rows)


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

    # Save / Stack into session input directory
    session_dir = _get_or_create_session_dir(payload.session_id)
    dest_path = session_dir / target_filename
    total_stacked_rows = _merge_and_save_csv(dest_path, canonical_csv, target_filename)

    canonical_bytes = canonical_csv.encode("utf-8")
    sha256_hash = hashlib.sha256(canonical_bytes).hexdigest()

    # Parse preview rows
    preview_reader = csv.DictReader(io.StringIO(canonical_csv))
    preview_rows = [dict(r) for idx, r in enumerate(preview_reader) if idx < 5]

    return {
        "filename": payload.filename,
        "mapped_filename": target_filename,
        "file_type": norm_type,
        "rows_count": total_stacked_rows,
        "headers": preview_reader.fieldnames or [],
        "checksum_sha256": sha256_hash,
        "preview_rows": preview_rows,
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
    total_stacked_rows = _merge_and_save_csv(dest_path, csv_text, target_filename)

    raw_bytes = csv_text.encode("utf-8")
    sha256_hash = hashlib.sha256(raw_bytes).hexdigest()

    return {
        "filename": payload.filename,
        "mapped_filename": target_filename,
        "file_type": doc_type,
        "rows_count": total_stacked_rows,
        "checksum_sha256": sha256_hash,
        "preview_rows": records[:5],
        "session_id": payload.session_id,
        "extractor": extracted.get("extractor", "multimodal_vision"),
        "confidence": extracted.get("confidence", 0.99),
        "status": "VALIDATED",
    }


class StreamExtractPayload(BaseModel):
    filename: str = Field(description="Name of the file to extract")
    content: str = Field(default="", description="Raw text or CSV content")
    content_base64: str = Field(default="", description="Base64 encoded PDF or image content")
    mime_type: str = Field(default="text/csv", description="MIME type")
    file_type: str = Field(default="auto", description="File category")
    session_id: str = Field(default="default_session", description="Session identifier")


@router.post("/stream-extract")
def stream_document_extraction(payload: StreamExtractPayload) -> StreamingResponse:
    """Stream live Python sandbox execution, task checklist, and terminal STDOUT via SSE."""
    from app.importers.sandbox_runner import run_sandbox_extraction_stream

    gen = run_sandbox_extraction_stream(
        filename=payload.filename,
        raw_content=payload.content,
        content_base64=payload.content_base64,
        mime_type=payload.mime_type,
        file_type=payload.file_type,
        session_id=payload.session_id,
    )
    return StreamingResponse(gen, media_type="text/event-stream")


class CommitExtractedPayload(BaseModel):
    session_id: str = Field(description="Session ID to save into")
    target_filename: str = Field(description="Normalized target filename (e.g. bank_entries.csv)")
    canonical_csv: str = Field(description="Normalized CSV text to save")


@router.post("/commit-extracted")
def commit_extracted_file(payload: CommitExtractedPayload) -> dict[str, Any]:
    """Commit verified canonical CSV data directly into the session directory."""
    if not payload.canonical_csv.strip():
        raise HTTPException(status_code=400, detail="Cannot commit empty canonical CSV.")

    session_dir = _get_or_create_session_dir(payload.session_id)
    dest_path = session_dir / payload.target_filename
    total_stacked_rows = _merge_and_save_csv(
        dest_path, payload.canonical_csv, payload.target_filename
    )

    canonical_bytes = payload.canonical_csv.encode("utf-8")
    sha256_hash = hashlib.sha256(canonical_bytes).hexdigest()

    return {
        "status": "COMMITTED",
        "target_filename": payload.target_filename,
        "rows_count": total_stacked_rows,
        "session_id": payload.session_id,
        "checksum_sha256": sha256_hash,
    }


class ReconcileSessionPayload(BaseModel):
    session_id: str = "default_session"
    fallback_profile: str = "dev"
    mode: Literal["rules-only", "agent"] = "agent"


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

    # Ensure standard schema files exist (empty template with headers if unprovided by user)
    standard_schemas = [
        (
            "payments.csv",
            [
                "payment_id",
                "order_id",
                "status",
                "currency",
                "gross_amount",
                "fee_amount",
                "tax_amount",
                "captured_at_utc",
                "settlement_id",
            ],
        ),
        (
            "refunds.csv",
            [
                "refund_id",
                "payment_id",
                "status",
                "currency",
                "refund_amount",
                "created_at_utc",
                "settlement_id",
            ],
        ),
        (
            "settlements.csv",
            [
                "settlement_id",
                "settled_at_utc",
                "window_start_utc",
                "window_end_utc",
                "status",
                "currency",
                "gross_credit",
                "fee_amount",
                "tax_amount",
                "adjustment_amount",
                "net_amount",
                "utr",
            ],
        ),
        (
            "bank_entries.csv",
            [
                "bank_entry_id",
                "posted_at_utc",
                "value_date",
                "currency",
                "signed_amount",
                "narration",
                "utr",
                "account_fingerprint",
            ],
        ),
        (
            "ledger_entries.csv",
            [
                "ledger_entry_id",
                "account_code",
                "accounting_date",
                "currency",
                "signed_amount",
                "source_reference",
                "source_type",
                "description",
                "entry_origin",
            ],
        ),
    ]

    for filename, headers in standard_schemas:
        target = session_dir / filename
        if not target.exists():
            with open(target, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)

    # Extract gateway settlement batch records if user uploaded combined Razorpay file
    settlements_file = session_dir / "settlements.csv"
    payments_file = session_dir / "payments.csv"
    bank_file = session_dir / "bank_entries.csv"

    if (
        not settlements_file.exists() or settlements_file.stat().st_size < 150
    ) and payments_file.exists():
        import decimal

        payments_by_settle: dict[str, list[dict[str, Any]]] = {}
        with open(payments_file, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                stl_id = r.get("settlement_id") or "stl_default_01"
                if stl_id not in payments_by_settle:
                    payments_by_settle[stl_id] = []
                payments_by_settle[stl_id].append(r)

        utr_by_settle: dict[str, str] = {}
        bank_utrs: list[str] = []
        if bank_file.exists():
            with open(bank_file, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    narration = r.get("narration", "")
                    utr = r.get("utr", "")
                    if utr:
                        bank_utrs.append(utr)
                        for stl_id in payments_by_settle:
                            if stl_id in narration:
                                utr_by_settle[stl_id] = utr

        with open(settlements_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "settlement_id",
                    "settled_at_utc",
                    "window_start_utc",
                    "window_end_utc",
                    "status",
                    "currency",
                    "gross_credit",
                    "fee_amount",
                    "tax_amount",
                    "adjustment_amount",
                    "net_amount",
                    "utr",
                ]
            )
            for idx, (stl_id, p_list) in enumerate(payments_by_settle.items()):
                gross_tot = sum(decimal.Decimal(p.get("gross_amount", "0")) for p in p_list)
                fee_tot = sum(decimal.Decimal(p.get("fee_amount", "0")) for p in p_list)
                tax_tot = sum(decimal.Decimal(p.get("tax_amount", "0")) for p in p_list)
                net_tot = gross_tot - fee_tot - tax_tot
                utr = utr_by_settle.get(stl_id) or (
                    bank_utrs[idx] if idx < len(bank_utrs) else f"UTR_RZP_{stl_id}"
                )
                ts = p_list[0].get("captured_at_utc", "2026-03-02T16:00:00Z")
                writer.writerow(
                    [
                        stl_id,
                        ts,
                        ts,
                        ts,
                        "PROCESSED",
                        "INR",
                        f"{gross_tot:.2f}",
                        f"{fee_tot:.2f}",
                        f"{tax_tot:.2f}",
                        "0.00",
                        f"{net_tot:.2f}",
                        utr,
                    ]
                )

    provider = _resolve_agent_provider() if payload.mode == "agent" else None

    try:
        res = execute_run(
            inputs_dir=session_dir,
            database=db,
            mode=payload.mode,
            provider=provider,
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
