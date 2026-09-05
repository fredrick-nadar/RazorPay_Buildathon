"""Provenance resolution for cited case evidence (PRD 6.3, 11.1, 11.2).

``case_evidence`` stores only a record type, a record id and a note. That is
enough to cite a record but not enough to trust the citation: a reader cannot
tell which immutable source row it came from, which revision of that row, or
whether the row was accepted or quarantined.

Every normalized table already persists ``source_row_number`` and
``content_hash`` pointing at the immutable ``source_rows`` entry. This module
is the single place that walks that pointer, so the Case Dossier, the Evidence
Trace, and any export all cite the same resolved provenance instead of each
view inventing its own join.

Resolution is read-only and fails soft per record: an evidence citation whose
normalized row is absent is reported as ``UNRESOLVED`` with a reason, never
silently dropped and never replaced by a placeholder.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from app.persistence.database import Database

__all__ = [
    "EvidenceProvenance",
    "resolve_case_evidence_provenance",
]

# record_type -> (normalized table, identity column, signed-amount column or None)
_RECORD_SOURCES: dict[str, tuple[str, str, str | None]] = {
    "PAYMENT": ("norm_payments", "payment_id", "gross_amount_paise"),
    "REFUND": ("norm_refunds", "refund_id", "refund_amount_paise"),
    "SETTLEMENT": ("norm_settlements", "settlement_id", "net_amount_paise"),
    "BANK_ENTRY": ("norm_bank_entries", "bank_entry_id", "signed_amount_paise"),
    "LEDGER_ENTRY": ("norm_ledger_entries", "ledger_entry_id", "signed_amount_paise"),
}


@dataclass(frozen=True)
class EvidenceProvenance:
    """One cited evidence record with its immutable source pointer."""

    record_type: str
    record_id: str
    note: str | None
    resolution: str
    resolution_reason: str | None
    run_id: str | None
    amount_paise: int | None
    content_hash: str | None
    source_row_number: int | None
    source_type: str | None
    source_file: str | None
    source_record_id: str | None
    source_state: str | None
    source_content_hash: str | None
    revision_matches_source: bool | None
    source_revision_id: str | None
    source_origin: str | None
    external_import_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _unresolved(
    record_type: str, record_id: str, note: str | None, reason: str
) -> EvidenceProvenance:
    return EvidenceProvenance(
        record_type=record_type,
        record_id=record_id,
        note=note,
        resolution="UNRESOLVED",
        resolution_reason=reason,
        run_id=None,
        amount_paise=None,
        content_hash=None,
        source_row_number=None,
        source_type=None,
        source_file=None,
        source_record_id=None,
        source_state=None,
        source_content_hash=None,
        revision_matches_source=None,
        source_revision_id=None,
        source_origin=None,
        external_import_id=None,
    )


def resolve_case_evidence_provenance(
    db: Database, case_id: str, run_id: str
) -> list[EvidenceProvenance]:
    """Resolve every evidence citation on ``case_id`` within ``run_id``.

    ``run_id`` scopes the normalized lookup, so a citation can never resolve
    against an identically named record belonging to a different run.
    """
    rows = db.query_all(
        "SELECT record_type, record_id, note FROM case_evidence "
        "WHERE case_id = ? ORDER BY rowid ASC",
        (case_id,),
    )

    run = db.query_one("SELECT summary_json FROM runs WHERE run_id = ?", (run_id,))
    source_revisions: dict[str, dict[str, Any]] = {}
    if run is not None:
        try:
            summary = json.loads(str(run["summary_json"]))
            source_revisions = {
                str(item["source_type"]): item
                for item in summary.get("source_provenance", {}).get("sources", [])
                if isinstance(item, dict) and item.get("source_type")
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            source_revisions = {}

    resolved: list[EvidenceProvenance] = []
    for row in rows:
        record_type = str(row["record_type"])
        record_id = str(row["record_id"])
        note = str(row["note"]) if row["note"] else None

        mapping = _RECORD_SOURCES.get(record_type)
        if mapping is None:
            resolved.append(_unresolved(record_type, record_id, note, "UNKNOWN_RECORD_TYPE"))
            continue

        table, identity_column, amount_column = mapping
        source_revision = source_revisions.get(
            {
                "PAYMENT": "payments",
                "REFUND": "refunds",
                "SETTLEMENT": "settlements",
                "BANK_ENTRY": "bank_entries",
                "LEDGER_ENTRY": "ledger_entries",
            }[record_type],
            {},
        )
        columns = ["run_id", "source_row_number", "content_hash"]
        if amount_column is not None:
            columns.append(amount_column)
        norm = db.query_one(
            f"SELECT {', '.join(columns)} FROM {table} "  # noqa: S608 - fixed allowlist
            f"WHERE run_id = ? AND {identity_column} = ?",
            (run_id, record_id),
        )
        if norm is None:
            resolved.append(
                _unresolved(record_type, record_id, note, "NORMALIZED_RECORD_NOT_FOUND")
            )
            continue

        source_row_number = int(norm["source_row_number"])
        content_hash = str(norm["content_hash"]) if norm["content_hash"] else None
        amount_paise = (
            int(norm[amount_column])
            if amount_column is not None and norm[amount_column] is not None
            else None
        )

        source = db.query_one(
            "SELECT source_type, source_file, source_record_id, content_hash, state "
            "FROM source_rows WHERE run_id = ? AND source_type = ? AND source_row_number = ?",
            # source_rows.source_type stores the singular record type
            # (PAYMENT, BANK_ENTRY, ...), the same vocabulary case_evidence uses.
            (run_id, record_type, source_row_number),
        )
        if source is None:
            resolved.append(
                EvidenceProvenance(
                    record_type=record_type,
                    record_id=record_id,
                    note=note,
                    resolution="PARTIAL",
                    resolution_reason="SOURCE_ROW_NOT_FOUND",
                    run_id=str(norm["run_id"]),
                    amount_paise=amount_paise,
                    content_hash=content_hash,
                    source_row_number=source_row_number,
                    source_type=None,
                    source_file=None,
                    source_record_id=None,
                    source_state=None,
                    source_content_hash=None,
                    revision_matches_source=None,
                    source_revision_id=(
                        str(source_revision["revision_id"])
                        if source_revision.get("revision_id")
                        else None
                    ),
                    source_origin=(
                        str(source_revision["origin"]) if source_revision.get("origin") else None
                    ),
                    external_import_id=(
                        str(source_revision["external_import_id"])
                        if source_revision.get("external_import_id")
                        else None
                    ),
                )
            )
            continue

        source_content_hash = str(source["content_hash"]) if source["content_hash"] else None
        resolved.append(
            EvidenceProvenance(
                record_type=record_type,
                record_id=record_id,
                note=note,
                resolution="RESOLVED",
                resolution_reason=None,
                run_id=str(norm["run_id"]),
                amount_paise=amount_paise,
                content_hash=content_hash,
                source_row_number=source_row_number,
                source_type=str(source["source_type"]),
                source_file=str(source["source_file"]) if source["source_file"] else None,
                source_record_id=(
                    str(source["source_record_id"]) if source["source_record_id"] else None
                ),
                source_state=str(source["state"]),
                source_content_hash=source_content_hash,
                revision_matches_source=(
                    None
                    if content_hash is None or source_content_hash is None
                    else content_hash == source_content_hash
                ),
                source_revision_id=(
                    str(source_revision["revision_id"])
                    if source_revision.get("revision_id")
                    else None
                ),
                source_origin=(
                    str(source_revision["origin"]) if source_revision.get("origin") else None
                ),
                external_import_id=(
                    str(source_revision["external_import_id"])
                    if source_revision.get("external_import_id")
                    else None
                ),
            )
        )
    return resolved
