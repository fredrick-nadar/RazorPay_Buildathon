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
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
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


class SourceRecoveryError(RuntimeError):
    """An authoritative activation is safe but its derived files need retry."""


@dataclass(frozen=True)
class RevisionActivation:
    revision_id: str
    reused: bool
    replaced_revision_id: str | None
    revision_number: int
    canonical_filename: str


@dataclass(frozen=True)
class SourceRevisionInput:
    source_type: SourceType
    original_filename: str
    raw_content: str
    canonical_csv: str
    accepted_count: int
    quarantined_count: int
    origin: str
    mapping: dict[str, str] | None = None
    external_import_id: str | None = None


_LOCKS_GUARD = threading.Lock()
_SESSION_LOCKS: dict[str, threading.RLock] = {}
_HELD_LOCKS = threading.local()


@contextmanager
def session_lock(session_dir: Path) -> Iterator[None]:
    """Reentrant thread + OS lock, released by the OS even on process termination."""
    key = str(session_dir.resolve())
    with _LOCKS_GUARD:
        local_lock = _SESSION_LOCKS.setdefault(key, threading.RLock())
    with local_lock:
        held: set[str] = getattr(_HELD_LOCKS, "paths", set())
        if key in held:
            yield
            return
        session_dir.mkdir(parents=True, exist_ok=True)
        with (session_dir / ".lock").open("a+b") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 10
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        posix_lock: Any = fcntl  # platform-dependent stubs on Windows
                        posix_lock.flock(handle, posix_lock.LOCK_EX | posix_lock.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise SourceRevisionError("Import session is busy; retry shortly.") from exc
                    time.sleep(0.05)
            _HELD_LOCKS.paths = held | {key}
            try:
                yield
            finally:
                _HELD_LOCKS.paths = held
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    posix_lock = fcntl
                    posix_lock.flock(handle, posix_lock.LOCK_UN)


def resolve_session_dir(settings: Settings, session_id: str, *, create: bool) -> Path:
    """Resolve a stable session directory namespaced to the configured database."""
    db_identity = hashlib.sha256(str(settings.db_path.resolve()).encode("utf-8")).hexdigest()[:16]
    session_dir = settings.import_staging_root / db_identity / session_id
    if create:
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SourceRevisionError(
                "Import staging directory is unavailable. "
                "Check its configured path and permissions."
            ) from exc
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
    _atomic_write(
        session_dir / MANIFEST_FILENAME,
        json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
    )


def _atomic_write(path: Path, content: bytes) -> None:
    """Never expose partially written bytes at a final name (including on retry)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".w-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_immutable(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise SourceRevisionError(f"Immutable source collision at {path.name!r}.")
        return
    # All callers hold the session OS lock; immutable names are never overwritten.
    _atomic_write(path, content)


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
    source = SourceRevisionInput(
        source_type,
        original_filename,
        raw_content,
        canonical_csv,
        accepted_count,
        quarantined_count,
        origin,
        mapping,
        external_import_id,
    )
    with session_lock(session_dir):
        try:
            result = stage_source_bundle(session_dir=session_dir, sources=[source])
        except OSError as exc:
            raise SourceRevisionError(
                "Evidence could not be saved; active sources were not changed. "
                "Check storage space, permissions and staging path length, then retry."
            ) from exc
        try:
            materialize_active_sources(session_dir)
        except OSError as exc:
            raise SourceRecoveryError(
                "Evidence activation is saved; derived CSV recovery is pending. "
                "Check storage and reopen this import."
            ) from exc
        return result[source_type]


def stage_source_bundle(
    *,
    session_dir: Path,
    sources: list[SourceRevisionInput],
    receipt: dict[str, Any] | None = None,
) -> dict[str, RevisionActivation]:
    """One manifest switch for all sources; any preparation failure leaves it unchanged.

    An optional durable outbox receipt is committed WITH the active pointers. SQLite
    projections can be replayed after a process crash; no distributed transaction
    between SQLite and the filesystem is assumed.
    """
    if not sources or len({source.source_type for source in sources}) != len(sources):
        raise SourceRevisionError("A bundle must contain distinct source types.")
    with session_lock(session_dir):
        manifest = load_manifest(session_dir)
        result: dict[str, RevisionActivation] = {
            source.source_type: _prepare_revision(session_dir, manifest, source)
            for source in sources
        }
        if receipt is not None:
            receipts = manifest.setdefault("activation_receipts", {})
            if not isinstance(receipts, dict):
                raise SourceRevisionError("Import activation receipts are invalid.")
            receipt = dict(receipt)
            receipt["source_revisions"] = {key: value.revision_id for key, value in result.items()}
            unchanged = all(
                row.reused and row.replaced_revision_id is None for row in result.values()
            )
            identical = any(
                {key: value for key, value in old.items() if key != "sequence"} == receipt
                for old in receipts.values()
            )
            if unchanged and identical:
                return result
            receipt["sequence"] = len(receipts) + 1
            material = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
            receipt_id = "act-" + hashlib.sha256(material.encode()).hexdigest()
            receipts.setdefault(receipt_id, receipt)
        _write_manifest(session_dir, manifest)
        return result


def _prepare_revision(
    session_dir: Path,
    manifest: dict[str, Any],
    source: SourceRevisionInput,
) -> RevisionActivation:
    source_type = source.source_type
    original_filename = source.original_filename
    raw_content, canonical_csv = source.raw_content, source.canonical_csv
    accepted_count, quarantined_count = source.accepted_count, source.quarantined_count
    origin, mapping, external_import_id = source.origin, source.mapping, source.external_import_id
    if source_type not in CANONICAL_FILENAMES:
        raise SourceRevisionError("Unknown source type.")
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

    with session_lock(session_dir):
        revisions: dict[str, Any] = manifest["revisions"]
        active_by_type: dict[str, str] = manifest["active_by_type"]
        previous_revision_id = active_by_type.get(source_type)
        reused = revision_id in revisions

        if reused:
            raw_relative = Path(str(revisions[revision_id]["raw_path"]))
            canonical_relative = Path(str(revisions[revision_id]["canonical_path"]))
        else:
            # Original names remain in metadata, never in deep on-disk paths.
            raw_relative = Path(".s") / f"{revision_id}.raw"
            canonical_relative = Path(".s") / f"{revision_id}.csv"
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
    with session_lock(session_dir):
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
            _atomic_write(destination, content)


def verified_active_sources(
    session_dir: Path, *, require_demo_metadata: bool = True
) -> dict[str, Any]:
    """Read one locked manifest and verify immutable active bytes, without writes."""
    with session_lock(session_dir):
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
                    if (
                        kind == "raw"
                        and revision["origin"] == "SYNTHETIC_DEMO"
                        and require_demo_metadata
                    ):
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
    with session_lock(session_dir):
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


def snapshot_active_sources(session_dir: Path, *, empty_refunds: str) -> Path:
    """Pin a hash-verified immutable input set; concurrent uploads cannot change a run."""
    with session_lock(session_dir):
        active = verified_active_sources(session_dir)
        identity = json.dumps(
            {key: row["revision_id"] for key, row in active.items()}, sort_keys=True
        )
        snapshot_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
        snapshot_dir = session_dir / ".runs" / snapshot_id
        for key, row in active.items():
            content = _contained_path(session_dir, row["canonical_path"]).read_bytes()
            _write_immutable(snapshot_dir / f"{key}.csv", content)
        if "refunds" not in active:
            _write_immutable(snapshot_dir / "refunds.csv", empty_refunds.encode("utf-8"))
        _write_immutable(
            snapshot_dir / ".evidence.json", json.dumps(active, sort_keys=True).encode("utf-8")
        )
        return snapshot_dir


def load_snapshot_provenance(inputs_dir: Path) -> dict[str, Any]:
    """Validate and minimize the immutable source manifest stored with a run snapshot."""
    path = inputs_dir / ".evidence.json"
    if not path.is_file():
        return {
            "manifest_present": False,
            "manifest_fingerprint": None,
            "contains_synthetic_demo": False,
            "production_eligible": False,
            "sources": [],
            "notice": "No intake revision manifest accompanies this synthetic dataset run.",
        }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceRevisionError("Run input provenance manifest is unreadable.") from exc
    if not isinstance(value, dict) or not value:
        raise SourceRevisionError("Run input provenance manifest is invalid.")

    sources: list[dict[str, Any]] = []
    for source_type, revision in sorted(value.items()):
        if source_type not in CANONICAL_FILENAMES or not isinstance(revision, dict):
            raise SourceRevisionError("Run input provenance manifest has an invalid source.")
        if revision.get("source_type") != source_type:
            raise SourceRevisionError("Run input provenance source identity does not match.")
        canonical_sha256 = str(revision.get("canonical_sha256") or "")
        canonical = inputs_dir / CANONICAL_FILENAMES[source_type]
        try:
            actual_sha256 = hashlib.sha256(canonical.read_bytes()).hexdigest()
        except OSError as exc:
            raise SourceRevisionError("A provenance-bound run input is unavailable.") from exc
        if len(canonical_sha256) != 64 or actual_sha256 != canonical_sha256:
            raise SourceRevisionError("A provenance-bound run input failed its hash check.")
        origin = str(revision.get("origin") or "")
        external_import_id = revision.get("external_import_id")
        row = {
            "source_type": source_type,
            "revision_id": str(revision.get("revision_id") or ""),
            "revision_number": int(revision.get("revision_number", 0)),
            "origin": origin,
            "external_import_id": (
                str(external_import_id) if external_import_id is not None else None
            ),
            "canonical_sha256": canonical_sha256,
            "raw_sha256": str(revision.get("raw_sha256") or ""),
            "accepted_count": int(revision.get("accepted_count", 0)),
            "quarantined_count": int(revision.get("quarantined_count", 0)),
        }
        if not row["revision_id"] or row["revision_number"] < 1:
            raise SourceRevisionError("Run input provenance revision identity is invalid.")
        if row["accepted_count"] < 0 or row["quarantined_count"] < 0:
            raise SourceRevisionError("Run input provenance row accounting is invalid.")
        if origin == "SYNTHETIC_DEMO":
            metadata = revision.get("demo_metadata")
            if (
                not isinstance(metadata, dict)
                or metadata.get("provenance") != "SYNTHETIC_DEMO"
                or metadata.get("canonical_filename") != CANONICAL_FILENAMES[source_type]
                or metadata.get("derived_from_gateway_import") != external_import_id
                or not metadata.get("manifest_hash")
            ):
                raise SourceRevisionError("Synthetic demo provenance binding is invalid.")
            row["demo_manifest_hash"] = str(metadata["manifest_hash"])
        sources.append(row)

    material = json.dumps(sources, sort_keys=True, separators=(",", ":"))
    contains_demo = any(row["origin"] == "SYNTHETIC_DEMO" for row in sources)
    return {
        "manifest_present": True,
        "manifest_fingerprint": hashlib.sha256(material.encode("utf-8")).hexdigest(),
        "contains_synthetic_demo": contains_demo,
        "production_eligible": False,
        "sources": sources,
        "notice": (
            "This run includes explicitly labelled ARGUS synthetic gateway evidence."
            if contains_demo
            else "This run uses the recorded Test Mode and merchant-upload source revisions."
        ),
    }


__all__ = [
    "CANONICAL_FILENAMES",
    "RevisionActivation",
    "SourceRevisionError",
    "SourceRecoveryError",
    "SourceType",
    "load_manifest",
    "load_snapshot_provenance",
    "materialize_active_sources",
    "resolve_session_dir",
    "session_source_status",
    "stage_source_revision",
]
