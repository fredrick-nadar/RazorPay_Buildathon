"""Shared session-readiness and durable reconciliation workflow boundary."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Literal

from app.ai.selection import resolve_investigator
from app.config import Settings
from app.importers.adapters import REFUND_SPEC
from app.importers.intake_activation import recover_session_activation
from app.importers.session_staging import (
    CANONICAL_FILENAMES,
    materialize_active_sources,
    resolve_session_dir,
    session_lock,
    session_source_status,
    snapshot_active_sources,
    verified_active_sources,
)
from app.persistence.database import Database
from app.workflow.controller import ReconciliationController

REQUIRED_SOURCE_LABELS = {
    "payments": "Razorpay payments",
    "settlements": "Razorpay settlements",
    "bank_entries": "bank statement",
    "ledger_entries": "merchant ledger",
}


def session_readiness(session_dir: Path) -> dict[str, Any]:
    with session_lock(session_dir):
        status = session_source_status(session_dir)
        if status["active_sources"]:
            verified_active_sources(session_dir, require_demo_metadata=False)
            materialize_active_sources(session_dir)
    active = status["active_sources"]
    missing = [label for source, label in REQUIRED_SOURCE_LABELS.items() if source not in active]
    empty = [
        label
        for source, label in REQUIRED_SOURCE_LABELS.items()
        if source in active and int(active[source]["accepted_count"]) == 0
    ]
    gateway_ready = all(
        source in active and int(active[source]["accepted_count"]) > 0
        for source in ("payments", "settlements")
    )
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


def empty_session_status(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "ready": False,
        "gateway_ready": False,
        "bank_ready": False,
        "ledger_ready": False,
        "ready_source_groups": 0,
        "missing_sources": list(REQUIRED_SOURCE_LABELS.values()),
        "empty_sources": [],
        "merchant_upload_required": [],
        "settlement_reconciliation_required": True,
        "lifecycle_state": "GATEWAY_IMPORT_REQUIRED",
        "gateway_import_id": None,
        "active_sources": {},
        "revision_counts": {source: 0 for source in CANONICAL_FILENAMES},
    }


def get_session_status(settings: Settings, database: Database, session_id: str) -> dict[str, Any]:
    session_dir = resolve_session_dir(settings, session_id, create=False)
    if not session_dir.is_dir():
        return empty_session_status(session_id)
    recover_session_activation(database, session_dir)
    return {"session_id": session_id, **session_readiness(session_dir)}


def not_ready_detail(readiness: dict[str, Any]) -> str:
    details: list[str] = []
    if readiness["missing_sources"]:
        details.append("missing: " + ", ".join(readiness["missing_sources"]))
    if readiness["empty_sources"]:
        details.append("no eligible rows: " + ", ".join(readiness["empty_sources"]))
    if readiness["merchant_upload_required"]:
        details.append(
            "separate merchant upload required: "
            + ", ".join(
                REQUIRED_SOURCE_LABELS[source] for source in readiness["merchant_upload_required"]
            )
        )
    return (
        "Full reconciliation is not ready (" + "; ".join(details) + "). "
        "Imported sources remain available, but ARGUS will not create a complete run "
        "until Razorpay, bank, and ledger evidence are present."
    )


def start_session_job(
    *,
    settings: Settings,
    database: Database,
    controller: ReconciliationController,
    session_id: str,
    mode: Literal["rules-only", "agent", "fake"],
) -> dict[str, Any]:
    session_dir = resolve_session_dir(settings, session_id, create=False)
    if not session_dir.is_dir():
        raise FileNotFoundError("No import session exists for this identifier.")

    selection_request = "none" if mode == "rules-only" else mode
    selection = resolve_investigator(settings, selection_request)
    with session_lock(session_dir):
        recover_session_activation(database, session_dir)
        readiness = session_readiness(session_dir)
        manifest = {
            "lifecycle_state": readiness["lifecycle_state"],
            "active_sources": readiness["active_sources"],
        }
        if not readiness["ready"]:
            job, reused = controller.create_blocked_job(
                session_id=session_id,
                snapshot_manifest=manifest,
                requested_mode=selection_request,
                provider_id=selection.provider_id,
                reason=not_ready_detail(readiness),
                policy_fingerprint=selection.policy_fingerprint,
            )
            return {**job, "reused": reused}
        refunds_buffer = io.StringIO(newline="")
        csv.writer(refunds_buffer).writerow(REFUND_SPEC.columns)
        snapshot = snapshot_active_sources(session_dir, empty_refunds=refunds_buffer.getvalue())

    job, reused = controller.create_job(
        session_id=session_id,
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


__all__ = [
    "REQUIRED_SOURCE_LABELS",
    "empty_session_status",
    "get_session_status",
    "not_ready_detail",
    "session_readiness",
    "start_session_job",
]
