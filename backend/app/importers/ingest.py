"""Dataset ingestion: read, validate, group, dedup, quarantine (PRD 8.1).

Guarantees:

- No row is ever silently dropped: every physical row becomes a
  :class:`SourceRowOutcome` in one of three states (ACCEPTED,
  DUPLICATE_DELIVERY, QUARANTINED), and accepted + quarantined + duplicate
  counts always equal the raw row count per file.
- Rows are grouped by (source type, source record id) BEFORE any record is
  canonicalized. Identical content hashes yield one economic record plus
  DUPLICATE_DELIVERY markers; differing content hashes quarantine every
  conflicting row as DUPLICATE_ID_CONFLICT - never "first row wins".
- Row numbers and file ordering never influence the accepted record set, the
  economic outputs, or content hashes.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

from app.domain.enums import QuarantineReason, SourceType
from app.domain.records import (
    AcceptedRecords,
    BankEntryRecord,
    LedgerEntryRecord,
    PaymentRecord,
    RefundRecord,
    SettlementRecord,
)
from app.importers.adapters import (
    ADAPTER_SPECS,
    AdapterSpec,
    QuarantineSignal,
    parse_bank_row,
    parse_ledger_row,
    parse_payment_row,
    parse_refund_row,
    parse_settlement_row,
)
from app.importers.normalize import content_hash

STATE_ACCEPTED = "ACCEPTED"
STATE_DUPLICATE = "DUPLICATE_DELIVERY"
STATE_QUARANTINED = "QUARANTINED"


class IngestError(RuntimeError):
    """File-level failure: the inputs directory cannot be ingested at all."""


@dataclass(frozen=True)
class SourceRowOutcome:
    """Terminal state of one physical source row."""

    source_type: SourceType
    source_row_number: int
    source_file: str
    source_record_id: str
    content_hash: str
    raw_payload_json: str
    state: str
    quarantine_reason: QuarantineReason | None = None
    quarantine_detail: str | None = None
    duplicate_of_row_number: int | None = None


@dataclass(frozen=True)
class FileIngestStats:
    file_stem: str
    raw_rows: int
    accepted: int
    quarantined: int
    duplicate_delivery: int
    sha256: str


@dataclass(frozen=True)
class IngestResult:
    rows: tuple[SourceRowOutcome, ...]
    records: AcceptedRecords
    file_stats: tuple[FileIngestStats, ...]
    inputs_fingerprint: str

    @property
    def raw_row_count(self) -> int:
        return sum(stat.raw_rows for stat in self.file_stats)

    @property
    def quarantined_count(self) -> int:
        return sum(stat.quarantined for stat in self.file_stats)

    @property
    def duplicate_delivery_count(self) -> int:
        return sum(stat.duplicate_delivery for stat in self.file_stats)

    @property
    def accepted_count(self) -> int:
        return self.records.total_count()


_PARSER_BY_STEM: dict[str, Callable[[dict[str, str], int, str], object]] = {
    "payments": parse_payment_row,
    "refunds": parse_refund_row,
    "settlements": parse_settlement_row,
    "bank_entries": parse_bank_row,
    "ledger_entries": parse_ledger_row,
}


@dataclass(frozen=True)
class _Candidate:
    row_number: int
    content_hash: str
    record_id: str
    record: object


def _read_rows(path: Path, spec: AdapterSpec) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, restkey="__extra__", restval=None)
        fieldnames = reader.fieldnames
        if fieldnames is None or tuple(fieldnames) != spec.columns:
            raise IngestError(
                f"{path.name}: header does not match the expected columns for {spec.file_stem}"
            )
        return [dict(row) for row in reader]


def _is_shape_valid(row: dict[str, str]) -> bool:
    if row.get("__extra__") is not None:
        return False
    return all(value is not None for value in row.values())


def _raw_payload(row: dict[str, str], spec: AdapterSpec) -> str:
    payload: dict[str, object] = {column: row.get(column) for column in spec.columns}
    if row.get("__extra__") is not None:
        payload["__extra__"] = row["__extra__"]
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _ingest_file(path: Path, spec: AdapterSpec) -> tuple[list[SourceRowOutcome], list[_Candidate]]:
    rows = _read_rows(path, spec)
    outcomes: list[SourceRowOutcome] = []
    candidates: list[_Candidate] = []
    relative = f"inputs/{path.name}"
    for index, row in enumerate(rows, start=1):
        digest = content_hash(spec.columns, row)
        record_id = (row.get(spec.id_column) or "").strip()
        payload = _raw_payload(row, spec)
        shape_valid = _is_shape_valid(row)
        parsed: object = None
        signal: QuarantineSignal | None = None
        if shape_valid:
            try:
                parsed = _PARSER_BY_STEM[spec.file_stem](row, index, relative)
            except QuarantineSignal as caught:
                signal = caught
        if parsed is not None:
            outcomes.append(
                SourceRowOutcome(
                    source_type=spec.source_type,
                    source_row_number=index,
                    source_file=relative,
                    source_record_id=record_id,
                    content_hash=digest,
                    raw_payload_json=payload,
                    state=STATE_ACCEPTED,
                )
            )
            candidates.append(_Candidate(index, digest, record_id, parsed))
        else:
            reason = signal.reason if signal is not None else QuarantineReason.INVALID_ROW_SHAPE
            detail = (
                signal.detail
                if signal is not None
                else "row does not match the declared column set"
            )
            outcomes.append(
                SourceRowOutcome(
                    source_type=spec.source_type,
                    source_row_number=index,
                    source_file=relative,
                    source_record_id=record_id,
                    content_hash=digest,
                    raw_payload_json=payload,
                    state=STATE_QUARANTINED,
                    quarantine_reason=reason,
                    quarantine_detail=detail,
                )
            )
    return outcomes, candidates


def _resolve_states(
    outcomes: list[SourceRowOutcome], candidates: list[_Candidate]
) -> tuple[list[SourceRowOutcome], set[int]]:
    """Apply id-grouping; return final outcomes plus accepted row numbers."""

    groups: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.record_id, []).append(candidate)

    accepted_rows: set[int] = set()
    duplicate_of: dict[int, int] = {}
    conflict_rows: set[int] = set()
    for record_id in sorted(groups):
        group = groups[record_id]
        hashes = {candidate.content_hash for candidate in group}
        if len(hashes) == 1:
            canonical = min(group, key=lambda candidate: candidate.row_number)
            accepted_rows.add(canonical.row_number)
            for candidate in group:
                if candidate.row_number != canonical.row_number:
                    duplicate_of[candidate.row_number] = canonical.row_number
        else:
            conflict_rows.update(candidate.row_number for candidate in group)

    final: list[SourceRowOutcome] = []
    for outcome in outcomes:
        row_number = outcome.source_row_number
        if row_number in conflict_rows:
            final.append(
                replace(
                    outcome,
                    state=STATE_QUARANTINED,
                    quarantine_reason=QuarantineReason.DUPLICATE_ID_CONFLICT,
                    quarantine_detail=("same source record id delivered with conflicting content"),
                    duplicate_of_row_number=None,
                )
            )
        elif row_number in duplicate_of:
            final.append(
                replace(
                    outcome,
                    state=STATE_DUPLICATE,
                    quarantine_reason=None,
                    quarantine_detail=None,
                    duplicate_of_row_number=duplicate_of[row_number],
                )
            )
        else:
            final.append(outcome)
    return final, accepted_rows


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def ingest_inputs(inputs_dir: Path) -> IngestResult:
    """Ingest a dataset ``inputs`` directory; never silently drops a row."""
    inputs_dir = Path(inputs_dir)
    if not inputs_dir.is_dir():
        raise IngestError(f"inputs directory not found: {inputs_dir}")
    csv_files = sorted(path.name for path in inputs_dir.glob("*.csv"))
    expected = sorted(f"{spec.file_stem}.csv" for spec in ADAPTER_SPECS)
    if csv_files != expected:
        raise IngestError(
            f"{inputs_dir}: expected exactly the five input CSVs {expected}, found {csv_files}"
        )

    all_outcomes: list[SourceRowOutcome] = []
    payments: list[PaymentRecord] = []
    refunds: list[RefundRecord] = []
    settlements: list[SettlementRecord] = []
    bank_entries: list[BankEntryRecord] = []
    ledger_entries: list[LedgerEntryRecord] = []
    file_stats: list[FileIngestStats] = []
    fingerprint_parts: list[str] = []

    for spec in ADAPTER_SPECS:
        path = inputs_dir / f"{spec.file_stem}.csv"
        outcomes, candidates = _ingest_file(path, spec)
        outcomes, accepted_rows = _resolve_states(outcomes, candidates)
        for candidate in candidates:
            if candidate.row_number not in accepted_rows:
                continue
            record = candidate.record
            if spec.source_type == SourceType.PAYMENT:
                payments.append(record)  # type: ignore[arg-type]
            elif spec.source_type == SourceType.REFUND:
                refunds.append(record)  # type: ignore[arg-type]
            elif spec.source_type == SourceType.SETTLEMENT:
                settlements.append(record)  # type: ignore[arg-type]
            elif spec.source_type == SourceType.BANK_ENTRY:
                bank_entries.append(record)  # type: ignore[arg-type]
            else:
                ledger_entries.append(record)  # type: ignore[arg-type]
        digest = _file_sha256(path)
        fingerprint_parts.append(f"{spec.file_stem}:{digest}")
        file_stats.append(
            FileIngestStats(
                file_stem=spec.file_stem,
                raw_rows=len(outcomes),
                accepted=sum(1 for outcome in outcomes if outcome.state == STATE_ACCEPTED),
                quarantined=sum(1 for outcome in outcomes if outcome.state == STATE_QUARANTINED),
                duplicate_delivery=sum(
                    1 for outcome in outcomes if outcome.state == STATE_DUPLICATE
                ),
                sha256=digest,
            )
        )
        all_outcomes.extend(outcomes)

    records = AcceptedRecords(
        payments=tuple(sorted(payments, key=lambda record: record.payment_id)),
        refunds=tuple(sorted(refunds, key=lambda record: record.refund_id)),
        settlements=tuple(sorted(settlements, key=lambda record: record.settlement_id)),
        bank_entries=tuple(sorted(bank_entries, key=lambda record: record.bank_entry_id)),
        ledger_entries=tuple(sorted(ledger_entries, key=lambda record: record.ledger_entry_id)),
    )
    fingerprint = sha256("|".join(fingerprint_parts).encode("utf-8")).hexdigest()
    return IngestResult(
        rows=tuple(all_outcomes),
        records=records,
        file_stats=tuple(file_stats),
        inputs_fingerprint=fingerprint,
    )
