"""Coordinate atomic source activation with recoverable SQLite projections.

The manifest is authoritative. A crash after its switch cannot leave a half
bundle: history/audit projections replay on the next session read or mutation.
Recovery never selects old revisions again and never activates merchant sources.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.importers.session_staging import (
    RevisionActivation,
    SourceRecoveryError,
    SourceRevisionError,
    SourceRevisionInput,
    load_manifest,
    materialize_active_sources,
    session_lock,
    stage_source_bundle,
)
from app.persistence.database import Database
from app.persistence.intake_activation import project_activation


class ActivationRecoveryError(SourceRecoveryError):
    """The manifest is committed but its derived projections still need recovery."""


def recover_session_activation(db: Database, session_dir: Path) -> None:
    if not session_dir.is_dir():
        return
    with session_lock(session_dir):
        receipts = load_manifest(session_dir).get("activation_receipts", {})
        if not isinstance(receipts, dict):
            raise SourceRevisionError("Import activation receipts are invalid.")
        try:
            for receipt_id, receipt in sorted(
                receipts.items(), key=lambda pair: pair[1]["sequence"]
            ):
                if receipt["session_id"] != session_dir.name:
                    raise ValueError("Receipt belongs to a different session.")
                project_activation(db, receipt_id, receipt)
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceRevisionError(
                "Activation receipt is invalid or conflicts with immutable history."
            ) from exc
        except (sqlite3.Error, OSError) as exc:
            raise ActivationRecoveryError(
                "Evidence activation is saved, but history/audit recovery is pending. "
                "Reopen this import to retry recovery; do not clear the database or uploads."
            ) from exc


def activate_gateway_bundle(
    db: Database,
    *,
    session_dir: Path,
    sources: list[SourceRevisionInput],
    receipt: dict[str, Any],
) -> dict[str, RevisionActivation]:
    if {source.source_type for source in sources} != {"payments", "refunds", "settlements"}:
        raise SourceRevisionError(
            "Gateway activation must contain exactly the three gateway sources."
        )
    with session_lock(session_dir):
        recover_session_activation(db, session_dir)
        try:
            activations = stage_source_bundle(
                session_dir=session_dir,
                sources=sources,
                receipt=receipt,
            )
        except OSError as exc:
            raise SourceRevisionError(
                "Evidence could not be saved; active sources were not changed. "
                "Check storage space, permissions and staging path length, then retry."
            ) from exc
        recover_session_activation(db, session_dir)
        try:
            materialize_active_sources(session_dir)
        except OSError as exc:
            raise ActivationRecoveryError(
                "Evidence activation is saved; derived CSV recovery is pending. "
                "Check storage and reopen this import."
            ) from exc
        return activations
