"""Razorpay Test Mode sync and diagnostic API routes for ARGUS CONTROL."""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, SecretStr, model_validator

from app.api.routes_ingest import SESSION_ID_PATTERN
from app.config import Settings
from app.domain.money import MoneyError, require_paise
from app.importers.csv_intake import get_or_create_session_dir, validate_canonical_rows
from app.importers.demo_settlement import (
    DEMO_BUNDLE_SOURCES,
    GATEWAY_DEMO_SOURCES,
    DemoEvidenceError,
    build_demo_evidence,
    derive_demo_activation,
)
from app.importers.intake_activation import activate_gateway_bundle, recover_session_activation
from app.importers.razorpay_client import RazorpayClient, RazorpayFetchResult
from app.importers.session_staging import (
    SourceRevisionError,
    SourceRevisionInput,
    SourceType,
    resolve_session_dir,
    verified_active_sources,
)
from app.persistence.database import Database
from app.persistence.gateway_imports import (
    DOSSIER_PAGE_LIMIT_DEFAULT,
    DOSSIER_PAGE_LIMIT_MAX,
    GatewayEntity,
    get_demo_evidence,
    get_gateway_entities,
    get_gateway_import,
    persist_gateway_snapshot,
)

router = APIRouter(prefix="/api/v1/razorpay", tags=["razorpay"])


def _require_resource(name: str, result: RazorpayFetchResult) -> RazorpayFetchResult:
    """Stop the read sequence at its first failure; never stage a partial response."""
    if result.success:
        return result
    raise HTTPException(
        status_code=(504 if result.error_code == "DEADLINE_EXCEEDED" else 502),
        detail=f"Razorpay {name} API failed: {result.reason}. No data was imported.",
    )


_INPUT_SCHEMAS: dict[str, tuple[str, ...]] = {
    "payments.csv": (
        "payment_id",
        "order_id",
        "status",
        "currency",
        "gross_amount",
        "fee_amount",
        "tax_amount",
        "captured_at_utc",
        "settlement_id",
    ),
    "refunds.csv": (
        "refund_id",
        "payment_id",
        "status",
        "currency",
        "refund_amount",
        "created_at_utc",
        "settlement_id",
    ),
    "settlements.csv": (
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
    ),
    "bank_entries.csv": (
        "bank_entry_id",
        "posted_at_utc",
        "value_date",
        "currency",
        "signed_amount",
        "narration",
        "utr",
        "account_fingerprint",
    ),
    "ledger_entries.csv": (
        "ledger_entry_id",
        "account_code",
        "accounting_date",
        "currency",
        "signed_amount",
        "source_reference",
        "source_type",
        "description",
        "entry_origin",
    ),
}


def _paise_text(value: Any) -> str:
    """Format an API integer-subunit value, or leave it invalid for quarantine."""
    if value is None:
        return ""
    try:
        checked = require_paise(value)
    except MoneyError:
        return ""
    sign = "-" if checked < 0 else ""
    absolute = abs(checked)
    return f"{sign}{absolute // 100}.{absolute % 100:02d}"


def _timestamp_text(value: Any) -> str:
    """Format a Razorpay Unix timestamp without supplying a replacement value."""
    if isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return ""
        if parsed.tzinfo is None:
            return ""
        return parsed.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, bool) or not isinstance(value, int):
        return ""
    try:
        return datetime.datetime.fromtimestamp(value, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return ""


def _payment_fee_text(payment: dict[str, Any]) -> tuple[str, str]:
    """Split Razorpay's fee-inclusive-of-tax field into fee and tax paise."""
    raw_fee = payment.get("fee")
    raw_tax = payment.get("tax")
    if raw_fee is None or raw_tax is None:
        return "", _paise_text(raw_tax)
    try:
        fee_including_tax = require_paise(raw_fee)
        tax = require_paise(raw_tax)
        fee_excluding_tax = int(fee_including_tax) - int(tax)
        if fee_excluding_tax < 0:
            return "", ""
    except MoneyError:
        return "", ""
    return _paise_text(fee_excluding_tax), _paise_text(tax)


def _paise_int(value: Any) -> int | None:
    try:
        return int(require_paise(value))
    except MoneyError:
        return None


def _recon_entity_index(
    recon_items: list[dict[str, Any]], entity_type: str
) -> dict[str, dict[str, Any]]:
    return {
        str(item["entity_id"]): item
        for item in recon_items
        if str(item.get("type") or "").lower() == entity_type
        and isinstance(item.get("entity_id"), str)
        and item["entity_id"]
    }


def _settlement_row_from_reconciliation(
    settlement: dict[str, Any], recon_items: list[dict[str, Any]]
) -> list[str]:
    """Build one canonical settlement solely from official Razorpay fields."""
    settlement_id = str(settlement.get("id") or "")
    rows = [item for item in recon_items if item.get("settlement_id") == settlement_id]
    if not rows:
        return [
            settlement_id,
            _timestamp_text(settlement.get("settled_at") or settlement.get("created_at")),
            "",
            "",
            str(settlement.get("status") or "").upper(),
            str(settlement.get("currency") or ""),
            "",
            _paise_text(settlement.get("fees")),
            _paise_text(settlement.get("tax")),
            "",
            _paise_text(settlement.get("amount")),
            str(settlement.get("utr") or ""),
        ]

    event_times = [
        value
        for item in rows
        if isinstance((value := item.get("created_at")), int) and not isinstance(value, bool)
    ]
    settled_times = [
        value
        for item in rows
        if isinstance((value := item.get("settled_at")), int) and not isinstance(value, bool)
    ]
    payment_amounts = [
        value
        for item in rows
        if str(item.get("type") or "").lower() == "payment"
        and (value := _paise_int(item.get("amount"))) is not None
    ]
    row_fees = [value for item in rows if (value := _paise_int(item.get("fee"))) is not None]
    row_taxes = [value for item in rows if (value := _paise_int(item.get("tax"))) is not None]
    net_amount = _paise_int(settlement.get("amount"))
    fees = _paise_int(settlement.get("fees"))
    taxes = _paise_int(settlement.get("tax"))
    fee_total = sum(row_fees) if row_fees else fees
    tax_total = sum(row_taxes) if row_taxes else taxes
    gross_credit = sum(payment_amounts) if payment_amounts else None
    adjustment = None
    if (
        net_amount is not None
        and gross_credit is not None
        and fee_total is not None
        and tax_total is not None
    ):
        adjustment = net_amount - gross_credit + fee_total + tax_total
    utr = str(settlement.get("utr") or "") or next(
        (str(item["settlement_utr"]) for item in rows if item.get("settlement_utr")), ""
    )
    currency = str(settlement.get("currency") or "") or next(
        (str(item["currency"]) for item in rows if item.get("currency")), ""
    )
    settled_at = max(settled_times) if settled_times else settlement.get("created_at")
    return [
        settlement_id,
        _timestamp_text(settled_at),
        _timestamp_text(min(event_times) if event_times else None),
        _timestamp_text(max(event_times) if event_times else None),
        str(settlement.get("status") or "").upper(),
        currency,
        _paise_text(gross_credit),
        _paise_text(fee_total),
        _paise_text(tax_total),
        _paise_text(adjustment),
        _paise_text(net_amount),
        utr,
    ]


def _render_api_inputs(
    payments: list[dict[str, Any]],
    refunds: list[dict[str, Any]],
    settlements: list[dict[str, Any]],
    settlement_recon: list[dict[str, Any]],
) -> dict[str, str]:
    """Render only gateway values returned by Razorpay; never synthesize evidence."""
    rows_by_file: dict[str, list[list[str]]] = {
        "payments.csv": [],
        "refunds.csv": [],
        "settlements.csv": [],
    }

    payment_recon = _recon_entity_index(settlement_recon, "payment")
    refund_recon = _recon_entity_index(settlement_recon, "refund")
    for payment in payments:
        fee_text, tax_text = _payment_fee_text(payment)
        recon = payment_recon.get(str(payment.get("id") or ""), {})
        rows_by_file["payments.csv"].append(
            [
                str(payment.get("id") or ""),
                str(payment.get("order_id") or ""),
                str(payment.get("status") or "").upper(),
                str(payment.get("currency") or ""),
                _paise_text(payment.get("amount")),
                fee_text,
                tax_text,
                _timestamp_text(payment.get("created_at")),
                str(payment.get("settlement_id") or recon.get("settlement_id") or ""),
            ]
        )

    for refund in refunds:
        recon = refund_recon.get(str(refund.get("id") or ""), {})
        rows_by_file["refunds.csv"].append(
            [
                str(refund.get("id") or ""),
                str(refund.get("payment_id") or ""),
                str(refund.get("status") or "").upper(),
                str(refund.get("currency") or ""),
                _paise_text(refund.get("amount")),
                _timestamp_text(refund.get("created_at")),
                str(refund.get("settlement_id") or recon.get("settlement_id") or ""),
            ]
        )

    for settlement in settlements:
        if _has_fields(settlement, ("gross_credit", "window_start_utc", "window_end_utc")):
            rows_by_file["settlements.csv"].append(
                [
                    str(settlement.get("id") or ""),
                    _timestamp_text(settlement.get("settled_at") or settlement.get("created_at")),
                    _timestamp_text(settlement.get("window_start_utc")),
                    _timestamp_text(settlement.get("window_end_utc")),
                    str(settlement.get("status") or "").upper(),
                    str(settlement.get("currency") or ""),
                    _paise_text(settlement.get("gross_credit")),
                    _paise_text(settlement.get("fees")),
                    _paise_text(settlement.get("tax")),
                    _paise_text(settlement.get("adjustment")),
                    _paise_text(settlement.get("amount")),
                    str(settlement.get("utr") or ""),
                ]
            )
        else:
            rows_by_file["settlements.csv"].append(
                _settlement_row_from_reconciliation(settlement, settlement_recon)
            )
    rendered: dict[str, str] = {}
    for filename, rows in rows_by_file.items():
        headers = _INPUT_SCHEMAS[filename]
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(rows)
        rendered[filename] = output.getvalue()
    return rendered


class RazorpaySyncRequest(BaseModel):
    key_id: str | None = Field(default=None, description="Request-scoped Razorpay Test Mode Key ID")
    key_secret: SecretStr | None = Field(
        default=None, description="Request-scoped Razorpay Test Mode Key Secret"
    )
    count: int = Field(
        default=1000,
        ge=1,
        le=1000,
        description="Maximum entities to fetch per resource across pagination",
    )
    session_id: str = Field(default="default_session", pattern=SESSION_ID_PATTERN)
    period_start: datetime.date = Field(
        default_factory=lambda: (
            datetime.datetime.now(datetime.UTC).date() - datetime.timedelta(days=30)
        )
    )
    period_end: datetime.date = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC).date()
    )
    auto_reconcile: bool = Field(
        default=False,
        description="Deprecated safety field; a gateway import never starts full reconciliation.",
    )

    @model_validator(mode="after")
    def validate_period(self) -> RazorpaySyncRequest:
        if self.period_end < self.period_start:
            raise ValueError("period_end cannot be before period_start")
        if (self.period_end - self.period_start).days > 366:
            raise ValueError("Razorpay import period cannot exceed 366 days")
        return self


class DemoEvidenceRequest(BaseModel):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)


@router.get("/status")
def get_razorpay_status(request: Request) -> dict[str, Any]:
    """Check configuration and diagnostic connectivity to Razorpay Test API."""
    settings: Settings = request.app.state.settings
    key_id = settings.razorpay_key_id
    secret = (
        settings.razorpay_key_secret.get_secret_value()
        if isinstance(settings.razorpay_key_secret, SecretStr)
        else None
    )
    client = RazorpayClient(key_id=key_id, key_secret=secret)

    masked_key = (
        f"{key_id[:8]}...{key_id[-4:]}" if key_id and len(key_id) > 12 else (key_id or None)
    )
    smoke = client.smoke_test()

    return {
        "configured": client.is_configured,
        "key_id_masked": masked_key,
        "base_url": client.BASE_URL,
        "smoke_test": smoke,
    }


def _has_fields(payload: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return all(payload.get(field) is not None for field in fields)


def _classify_gateway_entity(entity_type: str, payload: dict[str, Any]) -> tuple[bool, str | None]:
    if entity_type == "ORDER":
        return False, "ORDER_IS_NOT_A_PAYMENT"
    if entity_type == "PAYMENT":
        if payload.get("status") != "captured":
            return False, "PAYMENT_NOT_CAPTURED"
        if not _has_fields(payload, ("id", "amount", "currency", "created_at", "fee", "tax")):
            return False, "PAYMENT_MISSING_RECONCILIATION_FIELDS"
        return True, None
    if entity_type == "REFUND":
        if payload.get("status") != "processed":
            return False, "REFUND_NOT_PROCESSED"
        if not _has_fields(payload, ("id", "payment_id", "amount", "currency", "created_at")):
            return False, "REFUND_MISSING_RECONCILIATION_FIELDS"
        return True, None
    if entity_type == "SETTLEMENT_RECON":
        if not _has_fields(
            payload,
            ("entity_id", "type", "amount", "currency", "settled_at", "settlement_id"),
        ):
            return False, "SETTLEMENT_RECON_MISSING_FIELDS"
        return True, None
    if entity_type != "SETTLEMENT":
        return False, "UNKNOWN_GATEWAY_ENTITY_TYPE"
    if payload.get("status") != "processed":
        return False, "SETTLEMENT_NOT_PROCESSED"
    if not _has_fields(payload, ("id", "amount", "fees", "tax", "created_at")):
        return False, "SETTLEMENT_MISSING_RECONCILIATION_FIELDS"
    return True, None


def _stage_entities(
    collections: tuple[tuple[str, list[dict[str, Any]]], ...],
) -> tuple[list[GatewayEntity], dict[str, list[dict[str, Any]]]]:
    settlement_ids = {
        str(item.get("id"))
        for entity_type, items in collections
        if entity_type == "SETTLEMENT"
        for item in items
        if item.get("status") == "processed" and item.get("id")
    }
    settled_entity_ids = {
        str(item.get("entity_id"))
        for entity_type, items in collections
        if entity_type == "SETTLEMENT_RECON"
        for item in items
        if item.get("settlement_id") in settlement_ids and item.get("entity_id")
    }
    staged: list[GatewayEntity] = []
    eligible: dict[str, list[dict[str, Any]]] = {
        "PAYMENT": [],
        "REFUND": [],
        "SETTLEMENT": [],
        "SETTLEMENT_RECON": [],
    }
    for entity_type, items in collections:
        for item in items:
            is_eligible, reason = _classify_gateway_entity(entity_type, item)
            raw_id = item.get("id")
            if isinstance(raw_id, str) and raw_id:
                entity_id = raw_id
            elif entity_type == "SETTLEMENT_RECON":
                canonical = json.dumps(item, sort_keys=True, separators=(",", ":"))
                entity_id = f"recon-{hashlib.sha256(canonical.encode()).hexdigest()[:24]}"
            else:
                canonical = json.dumps(item, sort_keys=True, separators=(",", ":"))
                entity_id = f"missing-{hashlib.sha256(canonical.encode()).hexdigest()[:20]}"
                is_eligible = False
                reason = "MISSING_ENTITY_ID"
            if not is_eligible:
                readiness_state = "NOT_RECONCILIATION_ELIGIBLE"
            elif entity_type in {"PAYMENT", "REFUND"}:
                readiness_state = (
                    "SETTLEMENT_AVAILABLE"
                    if entity_id in settled_entity_ids
                    else "AWAITING_RAZORPAY_SETTLEMENT"
                )
            else:
                readiness_state = "SETTLEMENT_AVAILABLE"
            staged.append(
                GatewayEntity(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    payload=item,
                    reconciliation_eligible=is_eligible,
                    exclusion_reason=reason,
                    readiness_state=readiness_state,
                )
            )
            if is_eligible and entity_type in eligible:
                eligible[entity_type].append(item)
    return staged, eligible


@router.get("/imports/{import_id}")
def get_razorpay_import(
    import_id: str,
    request: Request,
    session_id: str | None = Query(
        default=None,
        pattern=SESSION_ID_PATTERN,
        description="Restore the labelled demo-evidence link for this import session",
    ),
    dossier_limit: int = Query(default=DOSSIER_PAGE_LIMIT_DEFAULT, ge=1, le=DOSSIER_PAGE_LIMIT_MAX),
    dossier_offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Re-read a staged snapshot so a reopened session restores persisted state."""
    db: Database = request.app.state.db
    session_error = None
    if session_id is not None:
        try:
            recover_session_activation(
                db, resolve_session_dir(request.app.state.settings, session_id, create=False)
            )
        except SourceRevisionError as exc:
            # Keep immutable history readable, but never claim corrupt evidence is active.
            session_error = str(exc)
    result = get_gateway_import(
        db, import_id, dossier_limit=dossier_limit, dossier_offset=dossier_offset
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Gateway import not found")
    result["demo_evidence"] = _demo_evidence_with_activation(
        db, request.app.state.settings, import_id=import_id, session_id=session_id
    )
    result["demo_generation"] = _demo_generation_availability(db, result)
    if session_error:
        result["session_integrity_error"] = session_error
        result["demo_generation"] = {"eligible": False, "reason": session_error}
    return result


def _demo_generation_availability(db: Database, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Preflight the exact generator; captured status alone is not sufficient."""
    if snapshot["provider"] != "RAZORPAY" or snapshot["mode"] != "TEST":
        return {"eligible": False, "reason": "Demo evidence requires Razorpay Test Mode."}
    if snapshot["counts"].get("SETTLEMENT", 0) or snapshot["counts"].get("SETTLEMENT_RECON", 0):
        return {
            "eligible": False,
            "reason": "Official settlement rows were returned; demo generation is disabled.",
        }
    try:
        bundle = build_demo_evidence(
            import_id=snapshot["import_id"],
            payments=get_gateway_entities(db, snapshot["import_id"], "PAYMENT"),
            refunds=get_gateway_entities(db, snapshot["import_id"], "REFUND"),
        )
        for source in GATEWAY_DEMO_SOURCES:
            _, quarantined, _ = validate_canonical_rows(bundle["files"][f"{source}.csv"], source)  # type: ignore[arg-type]
            if quarantined:
                raise DemoEvidenceError("Generated evidence would contain invalid rows.")
    except (DemoEvidenceError, ValueError, OverflowError, OSError):
        return {
            "eligible": False,
            "reason": (
                "Payment/refund data cannot safely generate a demo bundle. "
                "Check captured payments, INR amounts, fee, tax, IDs and timestamps."
            ),
        }
    return {"eligible": True, "reason": None}


def _demo_evidence_with_activation(
    db: Database, settings: Settings, *, import_id: str, session_id: str | None
) -> dict[str, Any] | None:
    """Return demo generation history together with its CURRENT activation state.

    The persisted row is history. Whether that bundle is still the session's
    active evidence is derived from the session manifest, so a superseded or
    partially replaced bundle can never be presented as active.
    """
    if session_id is None:
        return None
    evidence = get_demo_evidence(db, import_id=import_id, session_id=session_id)
    if evidence is None:
        return None
    expected_sources = (
        GATEWAY_DEMO_SOURCES if evidence["scope"] == "GATEWAY_ONLY" else DEMO_BUNDLE_SOURCES
    )
    session_dir = resolve_session_dir(settings, session_id, create=False)
    if session_dir is None or not session_dir.is_dir():
        # Missing storage is not proof that every source was replaced.
        return {
            **evidence,
            "activation_state": "UNKNOWN",
            "active_demo_sources": [],
            "superseded_sources": list(expected_sources),
            "expected_sources": list(expected_sources),
        }
    try:
        sources = verified_active_sources(session_dir)
    except SourceRevisionError:
        # A manifest we cannot read must never imply active demo evidence.
        return {
            **evidence,
            "activation_state": "UNKNOWN",
            "active_demo_sources": [],
            "superseded_sources": list(expected_sources),
            "expected_sources": list(expected_sources),
        }
    generation_metadata: dict[str, Any] = next(
        (
            source.get("demo_metadata", {})
            for source in sources.values()
            if source.get("origin") == "SYNTHETIC_DEMO"
            and source.get("external_import_id") == import_id
            and source.get("demo_metadata", {}).get("manifest_hash") == evidence["manifest_hash"]
        ),
        {},
    )
    return {
        **evidence,
        **derive_demo_activation(
            sources, import_id, evidence["manifest_hash"], expected_sources=expected_sources
        ),
        "input_counts": generation_metadata.get("input_counts"),
        "refund_exclusions": generation_metadata.get("refund_exclusions", []),
        "synthetic_policy": generation_metadata.get("synthetic_policy"),
    }


@router.post("/imports/{import_id}/generate-demo-evidence", deprecated=True)
@router.post("/imports/{import_id}/generate-gateway-evidence")
def generate_demo_evidence(
    import_id: str, payload: DemoEvidenceRequest, request: Request
) -> dict[str, Any]:
    """Stage gateway-only synthetic evidence; never generate or replace merchant files."""
    db: Database = request.app.state.db
    gateway_import = get_gateway_import(db, import_id)
    if gateway_import is None:
        raise HTTPException(status_code=404, detail="Gateway import not found")
    if gateway_import["provider"] != "RAZORPAY" or gateway_import["mode"] != "TEST":
        raise HTTPException(
            status_code=409,
            detail="Synthetic demo evidence is permanently disabled outside Razorpay Test Mode.",
        )
    counts = gateway_import["counts"]
    if int(counts.get("SETTLEMENT", 0)) or int(counts.get("SETTLEMENT_RECON", 0)):
        raise HTTPException(
            status_code=409,
            detail="Official settlement evidence already exists; demo evidence is not permitted.",
        )
    try:
        bundle = build_demo_evidence(
            import_id=import_id,
            payments=get_gateway_entities(db, import_id, "PAYMENT"),
            refunds=get_gateway_entities(db, import_id, "REFUND"),
        )
    except DemoEvidenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    settings: Settings = request.app.state.settings
    session_dir = get_or_create_session_dir(payload.session_id, settings)
    recover_session_activation(db, session_dir)
    existing = get_demo_evidence(db, import_id=import_id, session_id=payload.session_id)
    if (
        existing is not None
        and existing["scope"] == "GATEWAY_ONLY"
        and existing["manifest_hash"] != bundle["manifest_hash"]
    ):
        raise HTTPException(
            status_code=409, detail="Gateway demo content differs from its immutable history."
        )

    source_by_file: dict[str, SourceType] = {
        "payments.csv": "payments",
        "refunds.csv": "refunds",
        "settlements.csv": "settlements",
    }
    validation: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}
    for filename, source_type in source_by_file.items():
        accepted, quarantined, preview = validate_canonical_rows(
            bundle["files"][filename], source_type
        )
        if quarantined:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Generated {source_type} evidence failed deterministic validation; "
                    "no demo source was activated."
                ),
            )
        validation[filename] = (accepted, quarantined, preview)
    revisions: dict[str, dict[str, Any]] = {}
    sources: list[SourceRevisionInput] = []
    try:
        for filename, source_type in source_by_file.items():
            canonical_csv = bundle["files"][filename]
            accepted, quarantined, preview = validation[filename]
            sources.append(
                SourceRevisionInput(
                    source_type=source_type,
                    original_filename=f"synthetic-demo-{import_id}-{filename}",
                    raw_content=json.dumps(
                        {
                            "provenance": "SYNTHETIC_DEMO",
                            "derived_from_gateway_import": import_id,
                            "manifest_hash": bundle["manifest_hash"],
                            "canonical_filename": filename,
                            "scope": bundle["scope"],
                            "input_counts": bundle["input_counts"],
                            "refund_exclusions": bundle["refund_exclusions"],
                            "synthetic_policy": bundle["synthetic_policy"],
                        },
                        sort_keys=True,
                    ),
                    canonical_csv=canonical_csv,
                    accepted_count=accepted,
                    quarantined_count=quarantined,
                    origin="SYNTHETIC_DEMO",
                    external_import_id=import_id,
                )
            )
        activations = activate_gateway_bundle(
            db,
            session_dir=session_dir,
            sources=sources,
            receipt={
                "action": "SYNTHETIC_DEMO_EVIDENCE_STAGED",
                "import_id": import_id,
                "session_id": payload.session_id,
                "manifest_hash": bundle["manifest_hash"],
                "scope": bundle["scope"],
                "counts": {
                    key: bundle[key]
                    for key in (
                        "payments_count",
                        "refunds_count",
                        "settlements_count",
                    )
                },
                "input_counts": bundle["input_counts"],
                "refund_exclusions": bundle["refund_exclusions"],
                "synthetic_policy": bundle["synthetic_policy"],
            },
        )
        for filename, source_type in source_by_file.items():
            activation = activations[source_type]
            accepted, quarantined, preview = validation[filename]
            revisions[source_type] = {
                "revision_id": activation.revision_id,
                "revision_number": activation.revision_number,
                "reused": activation.reused,
                "accepted_count": accepted,
                "quarantined_count": quarantined,
                "quarantine_preview": preview,
            }
    except SourceRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    evidence = get_demo_evidence(db, import_id=import_id, session_id=payload.session_id)
    assert evidence is not None  # activate_gateway_bundle delivered the durable receipt
    evidence_id = evidence["evidence_id"]
    reused = existing is not None and existing["scope"] == "GATEWAY_ONLY"
    return {
        "success": True,
        "evidence_id": evidence_id,
        "reused": reused,
        "provenance": "SYNTHETIC_DEMO",
        "production_eligible": False,
        "manifest_hash": bundle["manifest_hash"],
        "source_revisions": revisions,
        "scope": bundle["scope"],
        "input_counts": bundle["input_counts"],
        "refund_exclusions": bundle["refund_exclusions"],
        "synthetic_policy": bundle["synthetic_policy"],
        "message": (
            "Labelled synthetic gateway evidence is ready: payments, refunds and settlements. "
            "Official Razorpay counts are unchanged. Bank and merchant ledger files were not "
            "generated or replaced; upload them separately. This evidence is derived from "
            "Test Mode IDs under an ARGUS synthetic policy, not issued by Razorpay and not "
            "proof of a bank receipt."
            + (
                f" {bundle['input_counts']['refunds_excluded']} refund record(s) were excluded "
                "with recorded reasons."
                if bundle["input_counts"]["refunds_excluded"]
                else ""
            )
        ),
    }


@router.post("/sync")
def sync_razorpay_data(payload: RazorpaySyncRequest, request: Request) -> dict[str, Any]:
    """Fetch and stage a read-only Razorpay Test Mode snapshot."""
    db: Database = request.app.state.db
    settings: Settings = request.app.state.settings

    provided_key_id = payload.key_id.strip() if payload.key_id else None
    provided_secret = payload.key_secret.get_secret_value().strip() if payload.key_secret else None
    if not provided_key_id or not provided_secret:
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide both the Razorpay Test Mode Key ID and Key Secret for this import. "
                "They are used only for this request and are not persisted."
            ),
        )
    client = RazorpayClient(key_id=provided_key_id, key_secret=provided_secret)

    if not client.is_configured:
        raise HTTPException(
            status_code=400,
            detail=("Razorpay Test Mode credentials are required for API import."),
        )
    from_timestamp = int(
        datetime.datetime.combine(payload.period_start, datetime.time.min, datetime.UTC).timestamp()
    )
    to_timestamp = int(
        datetime.datetime.combine(payload.period_end, datetime.time.max, datetime.UTC).timestamp()
    )
    resources = {
        "orders": _require_resource(
            "orders",
            client.fetch_all_orders(
                max_records=payload.count, from_ts=from_timestamp, to_ts=to_timestamp
            ),
        ),
        "payments": _require_resource(
            "payments",
            client.fetch_all_payments(
                max_records=payload.count, from_ts=from_timestamp, to_ts=to_timestamp
            ),
        ),
        "refunds": _require_resource(
            "refunds",
            client.fetch_all_refunds(
                max_records=payload.count, from_ts=from_timestamp, to_ts=to_timestamp
            ),
        ),
        "settlements": _require_resource(
            "settlements",
            client.fetch_all_settlements(
                max_records=payload.count, from_ts=from_timestamp, to_ts=to_timestamp
            ),
        ),
        "settlement_reconciliation": _require_resource(
            "settlement_reconciliation",
            client.fetch_settlement_reconciliation(
                period_start=payload.period_start,
                period_end=payload.period_end,
                max_records=payload.count,
            ),
        ),
    }
    order_items = resources["orders"].items
    payment_items = resources["payments"].items
    refund_items = resources["refunds"].items
    settlement_items = resources["settlements"].items
    settlement_recon_items = resources["settlement_reconciliation"].items

    staged, eligible = _stage_entities(
        (
            ("ORDER", order_items),
            ("PAYMENT", payment_items),
            ("REFUND", refund_items),
            ("SETTLEMENT", settlement_items),
            ("SETTLEMENT_RECON", settlement_recon_items),
        )
    )
    gateway_import = persist_gateway_snapshot(
        db,
        provider="RAZORPAY",
        mode="TEST",
        credential_identifier=str(client.key_id),
        entities=staged,
    )
    session_dir = get_or_create_session_dir(payload.session_id, settings)
    rendered_inputs = _render_api_inputs(
        eligible["PAYMENT"],
        eligible["REFUND"],
        eligible["SETTLEMENT"],
        eligible["SETTLEMENT_RECON"],
    )
    api_sources: dict[SourceType, tuple[str, Any]] = {
        "payments": ("payments.csv", payment_items),
        "refunds": ("refunds.csv", refund_items),
        "settlements": (
            "settlements.csv",
            {"settlements": settlement_items, "reconciliation": settlement_recon_items},
        ),
    }
    source_revisions: dict[str, dict[str, Any]] = {}
    sources = []
    validation = {}
    try:
        for source_type, (filename, raw_items) in api_sources.items():
            canonical_csv = rendered_inputs[filename]
            accepted_count, quarantined_count, quarantine_preview = validate_canonical_rows(
                canonical_csv, source_type
            )
            validation[source_type] = (accepted_count, quarantined_count, quarantine_preview)
            sources.append(
                SourceRevisionInput(
                    source_type=source_type,
                    original_filename=f"razorpay-{source_type}-{gateway_import.import_id}.json",
                    raw_content=json.dumps(raw_items, indent=2, sort_keys=True),
                    canonical_csv=canonical_csv,
                    accepted_count=accepted_count,
                    quarantined_count=quarantined_count,
                    origin="RAZORPAY_TEST_MODE",
                    external_import_id=gateway_import.import_id,
                )
            )
        activations = activate_gateway_bundle(
            db,
            session_dir=session_dir,
            sources=sources,
            receipt={
                "action": "GATEWAY_SNAPSHOT_STAGED",
                "import_id": gateway_import.import_id,
                "session_id": payload.session_id,
                "source_records_count": gateway_import.source_records_count,
                "reconciliation_eligible_count": gateway_import.reconciliation_eligible_count,
                "counts": gateway_import.counts,
                "period_start": payload.period_start.isoformat(),
                "period_end": payload.period_end.isoformat(),
            },
        )
        for source_type in api_sources:
            activation = activations[source_type]
            accepted_count, quarantined_count, quarantine_preview = validation[source_type]
            source_revisions[source_type] = {
                "revision_id": activation.revision_id,
                "revision_number": activation.revision_number,
                "reused": activation.reused,
                "replaced_revision_id": activation.replaced_revision_id,
                "accepted_count": accepted_count,
                "quarantined_count": quarantined_count,
                "quarantine_preview": quarantine_preview,
            }
    except SourceRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    import_dossier = get_gateway_import(db, gateway_import.import_id)
    if import_dossier is None:
        raise HTTPException(status_code=500, detail="Staged gateway import could not be read")

    gateway_ready = (
        source_revisions["payments"]["accepted_count"] > 0
        and source_revisions["settlements"]["accepted_count"] > 0
    )
    settlement_reconciliation_required = source_revisions["settlements"]["accepted_count"] == 0

    result: dict[str, Any] = {
        "success": True,
        "empty": gateway_import.source_records_count == 0,
        "orders_count": len(order_items),
        "payments_count": len(payment_items),
        "refunds_count": len(refund_items),
        "settlements_count": len(settlement_items),
        "settlement_reconciliation_count": len(settlement_recon_items),
        "period_start": payload.period_start.isoformat(),
        "period_end": payload.period_end.isoformat(),
        "data_source": "razorpay_test_mode",
        "provider_warning": None,
        "import_id": gateway_import.import_id,
        "import_status": "STAGED",
        "import_reused": gateway_import.reused,
        "source_records_count": gateway_import.source_records_count,
        "reconciliation_eligible_count": gateway_import.reconciliation_eligible_count,
        "reconciled": False,
        "gateway_ready": gateway_ready,
        "settlement_reconciliation_required": settlement_reconciliation_required,
        "source_revisions": source_revisions,
        "credential_source": "request_scoped",
        "credentials_persisted": False,
        "lifecycle_state": (
            "SETTLEMENT_AVAILABLE"
            if gateway_ready
            else (
                "AWAITING_RAZORPAY_SETTLEMENT"
                if source_revisions["payments"]["accepted_count"] > 0
                else "GATEWAY_IMPORT_REQUIRED"
            )
        ),
        "readiness_counts": import_dossier["readiness_counts"],
        "payment_dossier": import_dossier["payment_dossier"],
        "payment_dossier_total": import_dossier["payment_dossier_total"],
        "payment_dossier_limit": import_dossier["payment_dossier_limit"],
        "payment_dossier_offset": import_dossier["payment_dossier_offset"],
        "payment_dossier_truncated": import_dossier["payment_dossier_truncated"],
        "payment_counts": import_dossier["payment_counts"],
        "refund_counts": import_dossier["refund_counts"],
        "imported_at_utc": import_dossier["imported_at_utc"],
    }

    if source_revisions["payments"]["accepted_count"] == 0:
        result["message"] = (
            f"Imported {gateway_import.source_records_count} gateway source records; none are "
            "eligible captured payments for the selected period. Complete Test Mode payments "
            "through Razorpay Checkout or select a different period before reconciliation."
        )
    elif settlement_reconciliation_required:
        result["message"] = (
            f"Imported {gateway_import.source_records_count} gateway source records, including "
            f"{source_revisions['payments']['accepted_count']} eligible payments. Razorpay "
            "returned no complete settlement reconciliation for the selected period. Select a "
            "period containing processed settlements. Bank and merchant-ledger sources are "
            "still required."
        )
    else:
        result["message"] = (
            f"Imported {gateway_import.source_records_count} gateway source records; "
            f"{gateway_import.reconciliation_eligible_count} are gateway-eligible. "
            "Upload the bank statement and merchant ledger before full reconciliation."
        )

    return result
