"""Shared reviewed-CSV intake service for dashboard and trusted channel adapters."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

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
from app.importers.schema_mapping import DocumentType, canonicalize_with_mapping
from app.importers.session_staging import resolve_session_dir, session_lock, stage_source_revision
from app.persistence.database import Database

_PARSER_BY_TYPE = {
    "payments": (PAYMENT_SPEC, parse_payment_row),
    "refunds": (REFUND_SPEC, parse_refund_row),
    "settlements": (SETTLEMENT_SPEC, parse_settlement_row),
    "bank_entries": (BANK_SPEC, parse_bank_row),
    "ledger_entries": (LEDGER_SPEC, parse_ledger_row),
}


def get_or_create_session_dir(session_id: str, settings: Settings) -> Path:
    """Resolve the shared immutable staging namespace for an import session."""
    return resolve_session_dir(settings, session_id, create=True)


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


def commit_csv_evidence(
    *,
    settings: Settings,
    database: Database,
    filename: str,
    content: str,
    file_type: DocumentType,
    session_id: str,
    mapping: dict[str, str],
    origin: str = "MANUAL_CSV",
) -> dict[str, Any]:
    """Validate, canonicalize and atomically activate one immutable CSV revision."""
    canonical_csv, profile = canonicalize_with_mapping(
        content=content, document_type=file_type, mapping=mapping
    )
    accepted, quarantined, quarantine_preview = validate_canonical_rows(canonical_csv, file_type)
    session_dir = get_or_create_session_dir(session_id, settings)
    with session_lock(session_dir):
        recover_session_activation(database, session_dir)
        activation = stage_source_revision(
            session_dir=session_dir,
            source_type=file_type,
            original_filename=filename,
            raw_content=content,
            canonical_csv=canonical_csv,
            accepted_count=accepted,
            quarantined_count=quarantined,
            origin=origin,
            mapping=mapping,
        )

    preview_reader = csv.DictReader(io.StringIO(canonical_csv))
    preview_rows = [dict(row) for index, row in enumerate(preview_reader) if index < 5]
    return {
        "filename": filename,
        "mapped_filename": activation.canonical_filename,
        "file_type": file_type,
        "rows_count": len(profile.rows),
        "session_rows_count": len(profile.rows),
        "accepted_count": accepted,
        "quarantined_count": quarantined,
        "quarantine_preview": quarantine_preview,
        "checksum_sha256": profile.sha256,
        "preview_rows": preview_rows,
        "session_id": session_id,
        "status": "READY" if quarantined == 0 else "READY_WITH_WARNINGS",
        "reused": activation.reused,
        "revision_id": activation.revision_id,
        "revision_number": activation.revision_number,
        "replaced_revision_id": activation.replaced_revision_id,
        "active": True,
    }


__all__ = ["commit_csv_evidence", "get_or_create_session_dir", "validate_canonical_rows"]
