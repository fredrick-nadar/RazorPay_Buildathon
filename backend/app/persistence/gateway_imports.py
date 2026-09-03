"""Immutable staging repository for read-only payment-gateway snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from app.persistence.database import Database


@dataclass(frozen=True)
class GatewayEntity:
    entity_type: str
    entity_id: str
    payload: dict[str, Any]
    reconciliation_eligible: bool
    exclusion_reason: str | None = None
    readiness_state: str = "NOT_RECONCILIATION_ELIGIBLE"


@dataclass(frozen=True)
class GatewayImportResult:
    import_id: str
    reused: bool
    source_records_count: int
    reconciliation_eligible_count: int
    counts: dict[str, int]


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def persist_gateway_snapshot(
    db: Database,
    *,
    provider: str,
    mode: str,
    credential_identifier: str,
    entities: list[GatewayEntity],
) -> GatewayImportResult:
    """Persist one content-addressed snapshot without storing credentials.

    Re-fetching the identical remote state reuses the import and its immutable
    entity rows. A changed payload creates a new snapshot, preserving history.
    """
    ordered = sorted(entities, key=lambda item: (item.entity_type, item.entity_id))
    seen: set[tuple[str, str]] = set()
    encoded: list[tuple[GatewayEntity, str, str]] = []
    counts: dict[str, int] = {}
    for entity in ordered:
        key = (entity.entity_type, entity.entity_id)
        if key in seen:
            raise ValueError(f"duplicate gateway entity in snapshot: {key[0]}:{key[1]}")
        seen.add(key)
        raw = _canonical_payload(entity.payload)
        digest = sha256(raw.encode("utf-8")).hexdigest()
        encoded.append((entity, raw, digest))
        counts[entity.entity_type] = counts.get(entity.entity_type, 0) + 1

    snapshot_material = "|".join(
        f"{entity.entity_type}:{entity.entity_id}:{digest}" for entity, _, digest in encoded
    )
    snapshot_hash = sha256(snapshot_material.encode("utf-8")).hexdigest()
    credential_fingerprint = sha256(credential_identifier.encode("utf-8")).hexdigest()
    identity = f"{provider}|{mode}|{credential_fingerprint}|{snapshot_hash}"
    import_id = f"gwi-{sha256(identity.encode('utf-8')).hexdigest()[:20]}"
    eligible_count = sum(1 for entity, _, _ in encoded if entity.reconciliation_eligible)

    existing = db.query_one(
        "SELECT import_id FROM gateway_imports WHERE import_id = ?", (import_id,)
    )
    reused = existing is not None
    if not reused:
        with db.transaction():
            db.execute(
                "INSERT INTO gateway_imports (import_id, provider, mode, credential_fingerprint,"
                " snapshot_hash, status, source_records_count, reconciliation_eligible_count,"
                " counts_json, imported_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    import_id,
                    provider,
                    mode,
                    credential_fingerprint,
                    snapshot_hash,
                    "CAPTURED",
                    len(encoded),
                    eligible_count,
                    json.dumps(counts, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )
            db.execute_many(
                "INSERT INTO gateway_source_entities (import_id, entity_type, entity_id,"
                " content_hash, raw_payload_json, reconciliation_eligible, exclusion_reason,"
                " readiness_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        import_id,
                        entity.entity_type,
                        entity.entity_id,
                        digest,
                        raw,
                        int(entity.reconciliation_eligible),
                        entity.exclusion_reason,
                        entity.readiness_state,
                    )
                    for entity, raw, digest in encoded
                ],
            )

    return GatewayImportResult(
        import_id=import_id,
        reused=reused,
        source_records_count=len(encoded),
        reconciliation_eligible_count=eligible_count,
        counts=counts,
    )


def mark_gateway_import_staged(db: Database, import_id: str) -> None:
    """Mark a captured snapshot ready for reconciliation after source staging succeeds."""
    db.execute(
        "UPDATE gateway_imports SET status = 'STAGED' WHERE import_id = ?",
        (import_id,),
    )


DOSSIER_PAGE_LIMIT_DEFAULT = 25
DOSSIER_PAGE_LIMIT_MAX = 200


def _entity_counts(
    db: Database, import_id: str, entity_type: str, live_status: str
) -> dict[str, int]:
    """Count one entity type only, so no caller can mix populations.

    ``readiness_state`` is assigned to payments AND refunds alike, so an
    all-entity roll-up cannot answer "how many payments await settlement".
    Every number here is scoped to a single ``entity_type``.

    ``live_status`` is the provider status the demo generator selects on
    (``captured`` for payments, ``processed`` for refunds). It is counted
    separately from ``eligible`` because eligibility additionally requires the
    reconciliation fields, so the two populations can legitimately differ.
    """
    row = db.query_one(
        "SELECT COUNT(*) AS total,"
        " COALESCE(SUM(CASE WHEN json_extract(raw_payload_json, '$.status') = ?"
        " THEN 1 ELSE 0 END), 0) AS live_status,"
        " COALESCE(SUM(CASE WHEN reconciliation_eligible = 1 THEN 1 ELSE 0 END), 0) AS eligible,"
        " COALESCE(SUM(CASE WHEN reconciliation_eligible = 1"
        " AND readiness_state = 'AWAITING_RAZORPAY_SETTLEMENT' THEN 1 ELSE 0 END), 0)"
        " AS awaiting_settlement,"
        " COALESCE(SUM(CASE WHEN reconciliation_eligible = 1"
        " AND readiness_state = 'SETTLEMENT_AVAILABLE' THEN 1 ELSE 0 END), 0)"
        " AS settlement_available,"
        " COALESCE(SUM(CASE WHEN reconciliation_eligible = 0 THEN 1 ELSE 0 END), 0) AS not_eligible"
        " FROM gateway_source_entities WHERE import_id = ? AND entity_type = ?",
        (live_status, import_id, entity_type),
    )
    if row is None:
        return {
            "total": 0,
            live_status: 0,
            "eligible": 0,
            "awaiting_settlement": 0,
            "settlement_available": 0,
            "not_eligible": 0,
        }
    return {
        "total": int(row["total"]),
        # Named after the provider status so the population is unambiguous.
        live_status: int(row["live_status"]),
        "eligible": int(row["eligible"]),
        "awaiting_settlement": int(row["awaiting_settlement"]),
        "settlement_available": int(row["settlement_available"]),
        "not_eligible": int(row["not_eligible"]),
    }


def get_gateway_import(
    db: Database,
    import_id: str,
    *,
    dossier_limit: int = DOSSIER_PAGE_LIMIT_DEFAULT,
    dossier_offset: int = 0,
) -> dict[str, Any] | None:
    """Read one snapshot with an explicitly bounded, explicitly counted dossier page.

    The payment dossier is a PAGE, never the whole import. The caller is given
    the true total and the page window so no surface can imply that every
    imported record is on screen.
    """
    if dossier_limit < 1 or dossier_limit > DOSSIER_PAGE_LIMIT_MAX:
        raise ValueError(
            f"dossier_limit must be between 1 and {DOSSIER_PAGE_LIMIT_MAX}, got {dossier_limit}"
        )
    if dossier_offset < 0:
        raise ValueError(f"dossier_offset must not be negative, got {dossier_offset}")
    row = db.query_one(
        "SELECT import_id, provider, mode, status, source_records_count,"
        " reconciliation_eligible_count, counts_json, imported_at_utc"
        " FROM gateway_imports WHERE import_id = ?",
        (import_id,),
    )
    if row is None:
        return None
    excluded_rows = db.query_all(
        "SELECT entity_type, exclusion_reason, COUNT(*) AS count"
        " FROM gateway_source_entities WHERE import_id = ? AND reconciliation_eligible = 0"
        " GROUP BY entity_type, exclusion_reason ORDER BY entity_type, exclusion_reason",
        (import_id,),
    )
    readiness_rows = db.query_all(
        "SELECT readiness_state, COUNT(*) AS count FROM gateway_source_entities"
        " WHERE import_id = ? GROUP BY readiness_state ORDER BY readiness_state",
        (import_id,),
    )
    payment_counts = _entity_counts(db, import_id, "PAYMENT", "captured")
    refund_counts = _entity_counts(db, import_id, "REFUND", "processed")
    payment_total = payment_counts["total"]
    payment_rows = db.query_all(
        "SELECT entity_id, readiness_state, raw_payload_json FROM gateway_source_entities"
        " WHERE import_id = ? AND entity_type = 'PAYMENT'"
        " ORDER BY entity_id LIMIT ? OFFSET ?",
        (import_id, dossier_limit, dossier_offset),
    )
    return {
        "import_id": str(row["import_id"]),
        "provider": str(row["provider"]),
        "mode": str(row["mode"]),
        "status": str(row["status"]),
        "source_records_count": int(row["source_records_count"]),
        "reconciliation_eligible_count": int(row["reconciliation_eligible_count"]),
        "counts": json.loads(str(row["counts_json"])),
        "imported_at_utc": str(row["imported_at_utc"]),
        "readiness_counts": {
            str(item["readiness_state"]): int(item["count"]) for item in readiness_rows
        },
        "payment_dossier": [
            {
                "payment_id": str(item["entity_id"]),
                "readiness_state": str(item["readiness_state"]),
                "order_id": str(payload.get("order_id") or ""),
                "status": str(payload.get("status") or ""),
                "currency": str(payload.get("currency") or ""),
                "amount_paise": payload.get("amount"),
                "created_at": payload.get("created_at"),
            }
            for item in payment_rows
            for payload in [json.loads(str(item["raw_payload_json"]))]
        ],
        "payment_dossier_total": payment_total,
        "payment_dossier_limit": dossier_limit,
        "payment_dossier_offset": dossier_offset,
        "payment_dossier_truncated": dossier_offset + len(payment_rows) < payment_total,
        "payment_counts": payment_counts,
        "refund_counts": refund_counts,
        "excluded": [
            {
                "entity_type": str(item["entity_type"]),
                "reason": str(item["exclusion_reason"]),
                "count": int(item["count"]),
            }
            for item in excluded_rows
        ],
    }


def get_gateway_entities(db: Database, import_id: str, entity_type: str) -> list[dict[str, Any]]:
    """Read immutable gateway payloads for a bounded derived workflow."""
    rows = db.query_all(
        "SELECT raw_payload_json FROM gateway_source_entities"
        " WHERE import_id = ? AND entity_type = ? ORDER BY entity_id",
        (import_id, entity_type),
    )
    return [json.loads(str(row["raw_payload_json"])) for row in rows]


def record_demo_evidence(
    db: Database,
    *,
    import_id: str,
    session_id: str,
    manifest_hash: str,
    scope: str = "FULL_DEMO",
) -> tuple[str, bool]:
    if scope not in {"FULL_DEMO", "GATEWAY_ONLY"}:
        raise ValueError("Unknown demo evidence scope")
    identity = f"{import_id}|{session_id}|{manifest_hash}"
    evidence_id = f"demo-{sha256(identity.encode('utf-8')).hexdigest()[:20]}"
    existing = db.query_one(
        "SELECT evidence_id, manifest_hash FROM gateway_demo_evidence"
        " WHERE import_id = ? AND session_id = ? AND scope = ?",
        (import_id, session_id, scope),
    )
    if existing is not None:
        if str(existing["manifest_hash"]) != manifest_hash:
            raise ValueError("Demo evidence already exists with different content")
        return str(existing["evidence_id"]), True
    db.execute(
        "INSERT INTO gateway_demo_evidence"
        " (evidence_id, import_id, session_id, manifest_hash, created_at_utc, scope)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (evidence_id, import_id, session_id, manifest_hash, datetime.now(UTC).isoformat(), scope),
    )
    return evidence_id, False


def get_demo_evidence(db: Database, *, import_id: str, session_id: str) -> dict[str, Any] | None:
    """Read the labelled synthetic-demo record linking one import to one session.

    Reopening a session must be able to restore the SYNTHETIC_DEMO label from
    persisted state, never from transient client memory.
    """
    row = db.query_one(
        "SELECT evidence_id, manifest_hash, created_at_utc, scope FROM gateway_demo_evidence"
        " WHERE import_id = ? AND session_id = ?"
        " ORDER BY CASE scope WHEN 'GATEWAY_ONLY' THEN 0 ELSE 1 END LIMIT 1",
        (import_id, session_id),
    )
    if row is None:
        return None
    return {
        "evidence_id": str(row["evidence_id"]),
        "manifest_hash": str(row["manifest_hash"]),
        "created_at_utc": str(row["created_at_utc"]),
        "scope": str(row["scope"]),
        "provenance": "SYNTHETIC_DEMO",
        "production_eligible": False,
    }
