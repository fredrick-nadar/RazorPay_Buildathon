"""Durable, immutable source-revision staging for import sessions.

Raw uploads and their reviewed canonical CSVs are content-addressed and never
rewritten.  Selecting a replacement changes only ``active_by_type`` in the
session manifest; the derived canonical input files are materialized from that
manifest before readiness checks and reconciliation.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.config import Settings

SourceType = Literal[
    "payments",
    "refunds",
    "settlements",
    "bank_entries",
    "ledger_entries",
]

CANONICAL_FILENAMES: dict[SourceType, str] = {
    "payments": "payments.csv",
    "refunds": "refunds.csv",
    "settlements": "settlements.csv",
    "bank_entries": "bank_entries.csv",
    "ledger_entries": "ledger_entries.csv",
}

MANIFEST_FILENAME = ".source-revisions.json"
MANIFEST_VERSION = 1
_REVISION_REQUIRED_KEYS = {
    "revision_id",
    "revision_number",
    "source_type",
    "original_filename",
    "origin",
    "raw_sha256",
    "canonical_sha256",
    "raw_path",
    "canonical_path",
    "row_count",
    "accepted_count",
    "quarantined_count",
    "created_at_utc",
}


class SourceRevisionError(ValueError):
    """The immutable session-revision store is invalid or inconsistent."""


@dataclass(frozen=True)
class RevisionActivation:
    revision_id: str
    reused: bool
    replaced_revision_id: str | None
    revision_number: int
    canonical_filename: str


_LOCKS_GUARD = threading.Lock()
_SESSION_LOCKS: dict[str, threading.RLock] = {}


def _session_lock(session_dir: Path) -> threading.RLock:
    key = str(session_dir.resolve())
    with _LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(key, threading.RLock())


def resolve_session_dir(settings: Settings, session_id: str, *, create: bool) -> Path:
    """Resolve a stable session directory namespaced to the configured database."""
    db_identity = hashlib.sha256(str(settings.db_path.resolve()).encode("utf-8")).hexdigest()[:16]
    session_dir = settings.import_staging_root / db_identity / session_id
    if create:
        session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _empty_manifest() -> dict[str, Any]:
    return {"version": MANIFEST_VERSION, "active_by_type": {}, "revisions": {}}


def load_manifest(session_dir: Path) -> dict[str, Any]:
    """Load and structurally validate a session manifest; corruption is never ignored."""
    path = session_dir / MANIFEST_FILENAME
    if not path.is_file():
        return _empty_manifest()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceRevisionError("Import revision manifest is unreadable or corrupted.") from exc
    if not isinstance(value, dict) or value.get("version") != MANIFEST_VERSION:
        raise SourceRevisionError("Import revision manifest has an unsupported format.")
    active = value.get("active_by_type")
    revisions = value.get("revisions")
    if not isinstance(active, dict) or not isinstance(revisions, dict):
        raise SourceRevisionError("Import revision manifest is missing required sections.")
    for source_type, revision_id in active.items():
        if (
            source_type not in CANONICAL_FILENAMES
            or revision_id not in revisions
            or not isinstance(revisions[revision_id], dict)
            or revisions[revision_id].get("source_type") != source_type
        ):
            raise SourceRevisionError("Import revision manifest contains an invalid active source.")
    for revision_id, revision in revisions.items():
        if (
            not isinstance(revision, dict)
            or revision.get("revision_id") != revision_id
            or revision.get("source_type") not in CANONICAL_FILENAMES
            or not _REVISION_REQUIRED_KEYS.issubset(revision)
        ):
            raise SourceRevisionError("Import revision manifest contains an invalid revision.")
    return value


def _contained_path(session_dir: Path, relative_value: str) -> Path:
    candidate = (session_dir / relative_value).resolve()
    try:
        candidate.relative_to(session_dir.resolve())
    except ValueError as exc:
        raise SourceRevisionError("Import revision path escapes its session directory.") from exc
    return candidate


def _write_manifest(session_dir: Path, manifest: dict[str, Any]) -> None:
    path = session_dir / MANIFEST_FILENAME
    temporary = session_dir / f"{MANIFEST_FILENAME}.tmp"
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _safe_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)
    return cleaned[:120] or "source.bin"


def _write_immutable(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise SourceRevisionError(f"Immutable source collision at {path.name!r}.")
        return
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise SourceRevisionError(f"Immutable source collision at {path.name!r}.") from None


def _csv_row_count(canonical_csv: str) -> int:
    return sum(1 for _ in csv.DictReader(io.StringIO(canonical_csv)))


def stage_source_revision(
    *,
    session_dir: Path,
    source_type: SourceType,
    original_filename: str,
    raw_content: str,
    canonical_csv: str,
    accepted_count: int,
    quarantined_count: int,
    origin: str,
    mapping: dict[str, str] | None = None,
    external_import_id: str | None = None,
) -> RevisionActivation:
    """Preserve a source revision and make it the only active revision of its type."""
    if accepted_count < 0 or quarantined_count < 0:
        raise SourceRevisionError("Revision row counts cannot be negative.")
    row_count = _csv_row_count(canonical_csv)
    if accepted_count + quarantined_count != row_count:
        raise SourceRevisionError("Revision row accounting does not equal the canonical row count.")

    raw_bytes = raw_content.encode("utf-8")
    canonical_bytes = canonical_csv.encode("utf-8")
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    canonical_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
    identity = json.dumps(
        {
            "source_type": source_type,
            "raw_sha256": raw_sha256,
            "canonical_sha256": canonical_sha256,
            "mapping": mapping or {},
            "origin": origin,
            "external_import_id": external_import_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    revision_id = f"src-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"

    with _session_lock(session_dir):
        session_dir.mkdir(parents=True, exist_ok=True)
        manifest = load_manifest(session_dir)
        revisions: dict[str, Any] = manifest["revisions"]
        active_by_type: dict[str, str] = manifest["active_by_type"]
        previous_revision_id = active_by_type.get(source_type)
        reused = revision_id in revisions

        if reused:
            raw_relative = Path(str(revisions[revision_id]["raw_path"]))
            canonical_relative = Path(str(revisions[revision_id]["canonical_path"]))
        else:
            raw_relative = Path(".source") / f"{revision_id}-{_safe_filename(original_filename)}"
            canonical_relative = Path(".revisions") / source_type / f"{revision_id}.csv"
            revision_number = 1 + sum(
                1 for value in revisions.values() if value.get("source_type") == source_type
            )
            revisions[revision_id] = {
                "revision_id": revision_id,
                "revision_number": revision_number,
                "source_type": source_type,
                "original_filename": original_filename,
                "origin": origin,
                "external_import_id": external_import_id,
                "raw_sha256": raw_sha256,
                "canonical_sha256": canonical_sha256,
                "raw_path": raw_relative.as_posix(),
                "canonical_path": canonical_relative.as_posix(),
                "mapping": mapping or {},
                "row_count": row_count,
                "accepted_count": accepted_count,
                "quarantined_count": quarantined_count,
                "created_at_utc": datetime.now(UTC).isoformat(),
            }
        _write_immutable(_contained_path(session_dir, str(raw_relative)), raw_bytes)
        _write_immutable(_contained_path(session_dir, str(canonical_relative)), canonical_bytes)
        revision_number = int(revisions[revision_id]["revision_number"])
        active_by_type[source_type] = revision_id
        _write_manifest(session_dir, manifest)
        materialize_active_sources(session_dir)

    return RevisionActivation(
        revision_id=revision_id,
        reused=reused,
        replaced_revision_id=(
            previous_revision_id
            if previous_revision_id is not None and previous_revision_id != revision_id
            else None
        ),
        revision_number=revision_number,
        canonical_filename=CANONICAL_FILENAMES[source_type],
    )


def materialize_active_sources(session_dir: Path) -> None:
    """Rebuild derived input files from the manifest's active immutable revisions."""
    with _session_lock(session_dir):
        manifest = load_manifest(session_dir)
        for source_type, revision_id in manifest["active_by_type"].items():
            revision = manifest["revisions"][revision_id]
            canonical_path = _contained_path(session_dir, str(revision["canonical_path"]))
            if not canonical_path.is_file():
                raise SourceRevisionError(f"Active revision {revision_id!r} is missing its CSV.")
            content = canonical_path.read_bytes()
            if hashlib.sha256(content).hexdigest() != revision["canonical_sha256"]:
                raise SourceRevisionError(f"Active revision {revision_id!r} failed its hash check.")
            destination = session_dir / CANONICAL_FILENAMES[source_type]
            temporary = session_dir / f".{destination.name}.tmp"
            temporary.write_bytes(content)
            os.replace(temporary, destination)


def verified_active_sources(session_dir: Path) -> dict[str, Any]:
    """Read one locked manifest and verify immutable active bytes, without writes."""
    with _session_lock(session_dir):
        if not (session_dir / MANIFEST_FILENAME).is_file():
            raise SourceRevisionError("Import revision manifest is missing.")
        manifest = load_manifest(session_dir)
        active: dict[str, Any] = {}
        try:
            for source, revision_id in manifest["active_by_type"].items():
                revision = dict(manifest["revisions"][revision_id])
                for kind in ("raw", "canonical"):
                    content = _contained_path(
                        session_dir, str(revision[f"{kind}_path"])
                    ).read_bytes()
                    if hashlib.sha256(content).hexdigest() != revision[f"{kind}_sha256"]:
                        raise SourceRevisionError("Active source content failed its hash check.")
                    if kind == "raw" and revision["origin"] == "SYNTHETIC_DEMO":
                        metadata = json.loads(content)
                        if not isinstance(metadata, dict):
                            raise SourceRevisionError("Demo provenance metadata is invalid.")
                        revision["demo_metadata"] = metadata
                active[source] = revision
        except (OSError, ValueError, TypeError) as exc:
            raise SourceRevisionError("Active evidence could not be verified.") from exc
        return active


def session_source_status(session_dir: Path) -> dict[str, Any]:
    """Return active-source and revision metadata without exposing raw values."""
    with _session_lock(session_dir):
        manifest = load_manifest(session_dir)
        active_sources: dict[str, dict[str, Any]] = {}
        for source_type, revision_id in manifest["active_by_type"].items():
            revision = manifest["revisions"][revision_id]
            active_sources[source_type] = {
                key: revision[key]
                for key in (
                    "revision_id",
                    "revision_number",
                    "source_type",
                    "original_filename",
                    "origin",
                    "external_import_id",
                    "raw_sha256",
                    "canonical_sha256",
                    "row_count",
                    "accepted_count",
                    "quarantined_count",
                    "created_at_utc",
                )
            }
        revision_counts = {
            source_type: sum(
                1
                for revision in manifest["revisions"].values()
                if revision.get("source_type") == source_type
            )
            for source_type in CANONICAL_FILENAMES
        }
        return {"active_sources": active_sources, "revision_counts": revision_counts}


__all__ = [
    "CANONICAL_FILENAMES",
    "RevisionActivation",
    "SourceRevisionError",
    "SourceType",
    "load_manifest",
    "materialize_active_sources",
    "resolve_session_dir",
    "session_source_status",
    "stage_source_revision",
]
