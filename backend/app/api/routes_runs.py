"""Runs API routes for ARGUS CONTROL."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.ai.selection import InvestigatorUnavailableError, resolve_investigator
from app.audit.service import get_audit_trail
from app.config import Settings
from app.investigator.provider import InvestigatorProvider
from app.persistence.database import Database
from app.runs import execute_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])

# The five evidence sources ARGUS reconciles. The matrix reports all of them.
_MATRIX_RECORD_TYPES = frozenset({"PAYMENT", "REFUND", "SETTLEMENT", "BANK_ENTRY", "LEDGER_ENTRY"})

# Identifier-bearing fields a matrix search may match on. Amounts are excluded
# deliberately: searching money as text invites false matches on paise digits.
_MATRIX_SEARCH_FIELDS = (
    "record_id",
    "order_id",
    "payment_id",
    "settlement_id",
    "utr",
    "bank_entry_id",
    "ledger_entry_id",
    "account_code",
    "source_reference",
)


def _matrix_search_corpus(item: dict[str, Any]) -> str:
    """Lower-cased identifier text for one matrix row."""
    return " ".join(
        str(item[field]) for field in _MATRIX_SEARCH_FIELDS if item.get(field) is not None
    ).lower()


def _require_run(db: Database, run_id: str) -> Any:
    """Resolve a run id or fail closed.

    Every run-scoped read goes through this. Without it, `/cases`, `/matrix`
    and `/audit` answered HTTP 200 with empty content for a run that does not
    exist, so a deleted or mistyped selection was indistinguishable from a
    legitimately empty run and no view could offer a recovery action.
    """
    row = db.query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return row


def _run_view(row: Any) -> dict[str, Any]:
    """Return the single runtime-run contract consumed by every dashboard view."""
    return {
        "run_id": str(row["run_id"]),
        "tenant_id": str(row["tenant_id"]),
        "inputs_path": str(row["inputs_path"]),
        "status": str(row["status"]),
        "started_at_utc": str(row["started_at_utc"]),
        "finished_at_utc": str(row["finished_at_utc"]) if row["finished_at_utc"] else None,
        "economic_output_hash": str(row["economic_output_hash"])
        if row["economic_output_hash"]
        else None,
        "summary": json.loads(str(row["summary_json"])),
    }


def _resolve_agent_provider(
    settings: Settings, provider_id: str | None = None
) -> InvestigatorProvider:
    """Compatibility wrapper around the central, non-silent selection policy."""
    selection = resolve_investigator(settings, provider_id or "agent")
    if selection.provider is None:
        raise InvestigatorUnavailableError("agent mode requires an investigator provider")
    return selection.provider


class ReconcileRequest(BaseModel):
    dataset_profile: str = Field(
        default="dev", description="Dataset profile name under datasets/ (e.g. dev, adversarial)"
    )
    mode: Literal["rules-only", "agent"] = Field(
        default="rules-only", description="Reconciliation execution mode"
    )
    provider_id: str = Field(
        default="fake-deterministic-v1", description="Investigator provider id"
    )
    force: bool = Field(default=False, description="Force recomputation without reusing cached run")


@router.post("/reconcile")
def reconcile_dataset(payload: ReconcileRequest, request: Request) -> dict[str, Any]:
    db: Database = request.app.state.db
    settings: Settings = request.app.state.settings
    repo_root = Path(__file__).resolve().parents[3]
    inputs_path = repo_root / "datasets" / payload.dataset_profile / "inputs"

    if not inputs_path.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"dataset inputs directory not found at {inputs_path}",
        )

    try:
        provider = (
            _resolve_agent_provider(settings, payload.provider_id)
            if payload.mode == "agent"
            else None
        )
    except InvestigatorUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        res = execute_run(
            inputs_dir=inputs_path,
            database=db,
            mode=payload.mode,
            provider=provider,
            force=payload.force,
        )
        return {
            "run_id": res.run_id,
            "status": res.status.value,
            "reused": res.reused,
            "idempotency_key": res.idempotency_key,
            "economic_output_hash": res.economic_output_hash,
            "summary": res.summary,
        }
    except HTTPException:
        raise
    except Exception as exc:
        # Never place raw exception text in an HTTP body; the server log keeps
        # the detail and the client receives a stable, safe code.
        logger.exception("reconciliation request failed for profile %s", payload.dataset_profile)
        raise HTTPException(status_code=500, detail="RECONCILIATION_REQUEST_FAILED") from exc


@router.get("")
def list_runs(request: Request) -> list[dict[str, Any]]:
    db: Database = request.app.state.db
    rows = db.query_all(
        "SELECT run_id, tenant_id, inputs_path, status, started_at_utc, "
        "finished_at_utc, economic_output_hash, summary_json "
        "FROM runs ORDER BY rowid DESC LIMIT 50"
    )
    return [_run_view(row) for row in rows]


@router.get("/active")
def get_active_run(request: Request) -> dict[str, Any] | None:
    """Return the latest persisted run, or null before the first reconciliation."""
    db: Database = request.app.state.db
    row = db.query_one("SELECT * FROM runs ORDER BY rowid DESC LIMIT 1")
    return _run_view(row) if row is not None else None


@router.get("/{run_id}/summary")
def get_run_summary(run_id: str, request: Request) -> dict[str, Any]:
    db: Database = request.app.state.db
    return _run_view(_require_run(db, run_id))


@router.get("/{run_id}/cases")
def list_run_cases(
    run_id: str,
    request: Request,
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    db: Database = request.app.state.db
    _require_run(db, run_id)
    sql = "SELECT * FROM cases WHERE run_id = ?"
    params: list[Any] = [run_id]

    if status:
        sql += " AND status = ?"
        params.append(status)
    if category:
        sql += " AND category_candidate = ?"
        params.append(category)

    sql += " ORDER BY rowid ASC"
    rows = db.query_all(sql, params)

    cases = []
    for r in rows:
        case_id = str(r["case_id"])
        evidence_rows = db.query_all(
            "SELECT record_type, record_id, note FROM case_evidence WHERE case_id = ?", (case_id,)
        )
        evidence = [
            {
                "record_type": str(e["record_type"]),
                "record_id": str(e["record_id"]),
                "note": str(e["note"]) if e["note"] else None,
            }
            for e in evidence_rows
        ]
        cases.append(
            {
                "case_id": case_id,
                "run_id": str(r["run_id"]),
                "category": str(r["category_candidate"]),
                "status": str(r["status"]),
                "variance_paise": int(r["variance_paise"]),
                "affected_amount_paise": int(r["affected_amount_paise"]),
                "proposed_delta_paise": int(r["proposed_delta_paise"])
                if r["proposed_delta_paise"] is not None
                else None,
                "currency": str(r["currency"]),
                "summary": str(r["summary"]),
                "reason_codes": json.loads(str(r["reason_codes_json"])),
                "evidence": evidence,
                "opened_at_utc": str(r["opened_at_utc"]),
                "updated_at_utc": str(r["updated_at_utc"]),
            }
        )
    return cases


@router.get("/{run_id}/fee-audit")
def get_run_fee_audit(run_id: str, request: Request) -> dict[str, Any]:
    """Audit run fees against the CONFIGURED SYNTHETIC merchant policy.

    The rates are no longer request parameters. A caller previously supplied
    ``mdr_bps`` / ``gst_bps`` directly, so any client could choose the basis of
    a reported leakage figure and the response identified no policy at all. The
    active policy is now resolved from settings and returned with the numbers.
    """
    from app.domain.fee_audit import audit_run_fees
    from app.domain.fee_policy import resolve_fee_policy

    db: Database = request.app.state.db
    settings: Settings = request.app.state.settings
    _require_run(db, run_id)

    result = audit_run_fees(db, run_id, policy=resolve_fee_policy(settings))
    return result.model_dump()


@router.get("/{run_id}/dossier")
def get_run_dossier(run_id: str, request: Request) -> dict[str, Any]:
    import hashlib

    db: Database = request.app.state.db
    run_row = db.query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    if run_row is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")

    summary = json.loads(str(run_row["summary_json"]))

    # Fetch all cases and proofs
    case_rows = db.query_all("SELECT * FROM cases WHERE run_id = ? ORDER BY rowid ASC", (run_id,))
    cases = []
    total_variance_paise = 0
    case_status_counts: dict[str, int] = {}
    verifier_status_counts = {"PASS": 0, "FAIL": 0, "INCONCLUSIVE": 0, "NOT_RUN": 0}

    for cr in case_rows:
        cid = str(cr["case_id"])
        var_p = int(cr["variance_paise"])
        total_variance_paise += abs(var_p)
        case_status = str(cr["status"])
        case_status_counts[case_status] = case_status_counts.get(case_status, 0) + 1

        proof_row = db.query_one(
            "SELECT * FROM proofs WHERE case_id = ? ORDER BY rowid DESC LIMIT 1", (cid,)
        )
        proof = None
        if proof_row:
            verifier_status = str(proof_row["verifier_status"])
            verifier_status_counts[verifier_status] = (
                verifier_status_counts.get(verifier_status, 0) + 1
            )
            proof = {
                "proof_id": str(proof_row["proof_id"]),
                "claim": str(proof_row["claim"]),
                "category": str(proof_row["category"]),
                "verifier_status": str(proof_row["verifier_status"]),
                "verifier_rule_id": str(proof_row["verifier_rule_id"]),
                "proposed_delta_paise": int(proof_row["proposed_delta_paise"])
                if proof_row["proposed_delta_paise"] is not None
                else None,
                "authority_decision": str(proof_row["authority_decision"]),
                "canonical_hash": str(proof_row["canonical_hash"]),
            }
        else:
            verifier_status_counts["NOT_RUN"] += 1

        evidence_rows = db.query_all(
            "SELECT record_type, record_id, note FROM case_evidence WHERE case_id = ?", (cid,)
        )
        evidence = [
            {
                "record_type": str(e["record_type"]),
                "record_id": str(e["record_id"]),
                "note": str(e["note"]) if e["note"] else None,
            }
            for e in evidence_rows
        ]

        cases.append(
            {
                "case_id": cid,
                "category": str(cr["category_candidate"]),
                "status": case_status,
                "variance_paise": var_p,
                "affected_amount_paise": int(cr["affected_amount_paise"]),
                "proposed_delta_paise": int(cr["proposed_delta_paise"])
                if cr["proposed_delta_paise"] is not None
                else None,
                "summary": str(cr["summary"]),
                "proof": proof,
                "evidence": evidence,
                "opened_at_utc": str(cr["opened_at_utc"]),
            }
        )

    # Fetch audit logs
    audit_rows = db.query_all(
        "SELECT * FROM audit_log WHERE run_id = ? ORDER BY rowid ASC", (run_id,)
    )
    audit_events = [
        {
            "event_id": str(a["event_id"]),
            "action": str(a["action"]),
            "case_id": str(a["case_id"]) if a["case_id"] else None,
            "actor": str(a["actor"]),
            "timestamp_utc": str(a["timestamp_utc"]),
            "payload": json.loads(str(a["payload_json"])) if a["payload_json"] else {},
            "digest": str(a["digest"]),
        }
        for a in audit_rows
    ]

    # Export digest. This proves the returned dossier fields are internally
    # bound together; it is not an external audit or regulatory certificate.
    econ_hash = str(run_row["economic_output_hash"] or "none")
    source_provenance = summary.get("source_provenance")
    if not isinstance(source_provenance, dict):
        source_provenance = {
            "manifest_present": False,
            "manifest_fingerprint": None,
            "contains_synthetic_demo": False,
            "production_eligible": False,
            "sources": [],
            "notice": "No intake revision manifest was recorded for this run.",
        }
    source_fingerprint = source_provenance.get("manifest_fingerprint") or "none"
    raw_sig = (
        f"argus:dossier:v2:{run_id}:{econ_hash}:{len(cases)}:"
        f"{total_variance_paise}:{source_fingerprint}"
    )
    crypto_hash = hashlib.sha256(raw_sig.encode("utf-8")).hexdigest()

    return {
        "run_id": str(run_row["run_id"]),
        "tenant_id": str(run_row["tenant_id"]),
        "status": str(run_row["status"]),
        "started_at_utc": str(run_row["started_at_utc"]),
        "finished_at_utc": str(run_row["finished_at_utc"]) if run_row["finished_at_utc"] else None,
        "economic_output_hash": str(run_row["economic_output_hash"])
        if run_row["economic_output_hash"]
        else None,
        "dossier_digest": crypto_hash,
        "digest_algorithm": "SHA-256",
        "digest_scope": [
            "run_id",
            "economic_output_hash",
            "cases_count",
            "total_abs_case_variance_paise",
            "source_manifest_fingerprint",
        ],
        "summary": summary,
        "cases_count": len(cases),
        "total_abs_case_variance_paise": total_variance_paise,
        "runtime_metrics": {
            "eligible_record_count": summary.get("eligible_record_count"),
            "matched_record_count": summary.get("matched_record_count"),
            "runtime_match_rate": summary.get("runtime_match_rate"),
            "case_status_counts": dict(sorted(case_status_counts.items())),
            "verifier_status_counts": verifier_status_counts,
            "proof_count": sum(1 for case in cases if case["proof"] is not None),
            "audit_event_count": len(audit_events),
        },
        "cases": cases,
        "audit_trail": audit_events,
        "provenance": {
            "scope": "ACTIVE_RUN_RUNTIME",
            "data_classification": "SYNTHETIC_ONLY",
            "evaluator_labels_used": False,
            "external_audit_performed": False,
            "regulatory_certification": False,
            "money_representation": "SIGNED_INTEGER_PAISE",
            "source_rows_immutable": True,
            "source_manifest": source_provenance,
            "notice": (
                "This dossier reports reconciliation evidence and internal consistency only; "
                "it is not an external audit or regulatory certification."
            ),
        },
    }


@router.get("/{run_id}/matrix")
def get_run_matrix(
    run_id: str,
    request: Request,
    page: int = Query(default=1, ge=1, description="Page number"),
    limit: int = Query(default=50, ge=10, le=200, description="Records per page"),
    search: str = Query(default="", description="Search by ID or reference"),
    record_type: str = Query(
        default="ALL",
        description="ALL, PAYMENT, REFUND, SETTLEMENT, BANK_ENTRY or LEDGER_ENTRY",
    ),
    link_state: str = Query(default="ALL", description="ALL, RECONCILED or UNMATCHED"),
) -> dict[str, Any]:
    """Return the run's full normalized record inventory across all five sources.

    This endpoint previously anchored on ``norm_payments`` and skipped any
    payment whose match/settlement/bank/ledger chain was incomplete, and never
    read ``norm_refunds`` at all. For the dev fixture it therefore reported 84
    records out of 282 and presented that subset as the reconciled matrix, so
    198 records and every unmatched row were invisible.

    Now every payment, refund, settlement, bank entry and ledger entry in the
    run appears exactly once, each carrying its own link state, and the response
    declares the per-type census so a reader can see the whole population and
    what share of it is linked.
    """
    db: Database = request.app.state.db
    _require_run(db, run_id)

    requested_type = record_type.upper()
    if requested_type not in _MATRIX_RECORD_TYPES | {"ALL"}:
        raise HTTPException(status_code=400, detail="UNKNOWN_RECORD_TYPE")
    requested_link_state = link_state.upper()
    if requested_link_state not in {"ALL", "RECONCILED", "UNMATCHED"}:
        raise HTTPException(status_code=400, detail="UNKNOWN_LINK_STATE")

    matched_records = {
        (str(row["record_type"]), str(row["record_id"]))
        for row in db.query_all(
            "SELECT mm.record_type, mm.record_id FROM match_members mm "
            "JOIN match_groups mg ON mm.match_id = mg.match_id "
            "WHERE mg.run_id = ?",
            (run_id,),
        )
    }
    rule_by_record: dict[tuple[str, str], str] = {}
    for row in db.query_all(
        "SELECT mm.record_type, mm.record_id, mg.rule_id FROM match_members mm "
        "JOIN match_groups mg ON mm.match_id = mg.match_id "
        "WHERE mg.run_id = ?",
        (run_id,),
    ):
        key = (str(row["record_type"]), str(row["record_id"]))
        rule_by_record.setdefault(key, str(row["rule_id"]))

    settlements = {
        str(row["settlement_id"]): row
        for row in db.query_all("SELECT * FROM norm_settlements WHERE run_id = ?", (run_id,))
    }
    bank_by_utr: dict[str, list[Any]] = {}
    for row in db.query_all("SELECT * FROM norm_bank_entries WHERE run_id = ?", (run_id,)):
        if row["utr"]:
            bank_by_utr.setdefault(str(row["utr"]), []).append(row)
    ledger_by_reference: dict[str, list[Any]] = {}
    for row in db.query_all("SELECT * FROM norm_ledger_entries WHERE run_id = ?", (run_id,)):
        if row["source_reference"]:
            ledger_by_reference.setdefault(str(row["source_reference"]), []).append(row)

    records: list[dict[str, Any]] = []
    census: dict[str, dict[str, int]] = {}

    def _count(kind: str, linked: bool) -> None:
        bucket = census.setdefault(kind, {"total": 0, "reconciled": 0, "unmatched": 0})
        bucket["total"] += 1
        bucket["reconciled" if linked else "unmatched"] += 1

    def _base(
        kind: str,
        record_id: str,
        *,
        amount_paise: int,
        occurred_at_utc: str | None,
        content_hash: str | None,
        source_row_number: int,
        linked: bool,
        counterparties: dict[str, Any],
        missing_links: list[str],
    ) -> dict[str, Any]:
        return {
            "record_type": kind,
            "record_id": record_id,
            "run_id": run_id,
            "signed_amount_paise": amount_paise,
            "occurred_at_utc": occurred_at_utc,
            "content_hash": content_hash,
            "source_row_number": source_row_number,
            "link_state": "RECONCILED" if linked else "UNMATCHED",
            "match_rule": rule_by_record.get((kind, record_id)),
            "missing_links": missing_links,
            **counterparties,
        }

    for row in db.query_all(
        "SELECT * FROM norm_payments WHERE run_id = ? ORDER BY source_row_number ASC",
        (run_id,),
    ):
        payment_id = str(row["payment_id"])
        settlement_id = str(row["settlement_id"]) if row["settlement_id"] else None
        settlement = settlements.get(settlement_id) if settlement_id else None
        utr = str(settlement["utr"]) if settlement is not None and settlement["utr"] else None
        banks = bank_by_utr.get(utr, []) if utr else []
        ledgers = ledger_by_reference.get(payment_id, [])
        bank = banks[0] if len(banks) == 1 else None
        ledger = ledgers[0] if len(ledgers) == 1 else None
        gross = int(row["gross_amount_paise"])
        fee = int(row["fee_paise"])
        tax = int(row["tax_paise"])

        missing: list[str] = []
        if ("PAYMENT", payment_id) not in matched_records:
            missing.append("NO_MATCH_GROUP")
        if settlement is None:
            missing.append("NO_SETTLEMENT")
        if len(banks) > 1:
            missing.append("NON_UNIQUE_BANK_ENTRY")
        elif bank is None:
            missing.append("NO_BANK_ENTRY")
        if len(ledgers) > 1:
            missing.append("NON_UNIQUE_LEDGER_ENTRY")
        elif ledger is None:
            missing.append("NO_LEDGER_ENTRY")
        linked = not missing
        _count("PAYMENT", linked)
        records.append(
            _base(
                "PAYMENT",
                payment_id,
                amount_paise=gross,
                occurred_at_utc=str(row["captured_at_utc"]),
                content_hash=str(row["content_hash"]) if row["content_hash"] else None,
                source_row_number=int(row["source_row_number"]),
                linked=linked,
                missing_links=missing,
                counterparties={
                    "order_id": str(row["order_id"]) if row["order_id"] else None,
                    "status": str(row["status"]),
                    "gross_amount_paise": gross,
                    "fee_paise": fee,
                    "tax_paise": tax,
                    "net_amount_paise": gross - fee - tax,
                    "settlement_id": settlement_id,
                    "settlement_gross_paise": (
                        int(settlement["gross_credit_paise"]) if settlement is not None else None
                    ),
                    "utr": utr,
                    "bank_entry_id": str(bank["bank_entry_id"]) if bank is not None else None,
                    "bank_amount_paise": (
                        int(bank["signed_amount_paise"]) if bank is not None else None
                    ),
                    "ledger_entry_id": (
                        str(ledger["ledger_entry_id"]) if ledger is not None else None
                    ),
                    "ledger_amount_paise": (
                        int(ledger["signed_amount_paise"]) if ledger is not None else None
                    ),
                    "account_code": str(ledger["account_code"]) if ledger is not None else None,
                },
            )
        )

    for row in db.query_all(
        "SELECT * FROM norm_refunds WHERE run_id = ? ORDER BY source_row_number ASC",
        (run_id,),
    ):
        refund_id = str(row["refund_id"])
        settlement_id = str(row["settlement_id"]) if row["settlement_id"] else None
        missing = [] if ("REFUND", refund_id) in matched_records else ["NO_MATCH_GROUP"]
        if settlement_id is None or settlement_id not in settlements:
            missing.append("NO_SETTLEMENT")
        linked = not missing
        _count("REFUND", linked)
        records.append(
            _base(
                "REFUND",
                refund_id,
                # A refund reduces merchant receipts; carry it signed negative.
                amount_paise=-int(row["refund_amount_paise"]),
                occurred_at_utc=str(row["created_at_utc"]),
                content_hash=str(row["content_hash"]) if row["content_hash"] else None,
                source_row_number=int(row["source_row_number"]),
                linked=linked,
                missing_links=missing,
                counterparties={
                    "status": str(row["status"]),
                    "payment_id": str(row["payment_id"]) if row["payment_id"] else None,
                    "refund_amount_paise": int(row["refund_amount_paise"]),
                    "settlement_id": settlement_id,
                },
            )
        )

    for row in settlements.values():
        settlement_id = str(row["settlement_id"])
        utr = str(row["utr"]) if row["utr"] else None
        banks = bank_by_utr.get(utr, []) if utr else []
        bank = banks[0] if len(banks) == 1 else None
        missing = [] if ("SETTLEMENT", settlement_id) in matched_records else ["NO_MATCH_GROUP"]
        if len(banks) > 1:
            missing.append("NON_UNIQUE_BANK_ENTRY")
        elif bank is None:
            missing.append("NO_BANK_ENTRY")
        linked = not missing
        _count("SETTLEMENT", linked)
        records.append(
            _base(
                "SETTLEMENT",
                settlement_id,
                amount_paise=int(row["net_amount_paise"]),
                occurred_at_utc=str(row["settled_at_utc"]),
                content_hash=str(row["content_hash"]) if row["content_hash"] else None,
                source_row_number=int(row["source_row_number"]),
                linked=linked,
                missing_links=missing,
                counterparties={
                    "status": str(row["status"]),
                    "utr": utr,
                    "gross_credit_paise": int(row["gross_credit_paise"]),
                    "fee_paise": int(row["fee_paise"]),
                    "tax_paise": int(row["tax_paise"]),
                    "adjustment_paise": int(row["adjustment_paise"]),
                    "window_start_utc": str(row["window_start_utc"]),
                    "window_end_utc": str(row["window_end_utc"]),
                    "bank_entry_id": str(bank["bank_entry_id"]) if bank is not None else None,
                },
            )
        )

    for row in db.query_all(
        "SELECT * FROM norm_bank_entries WHERE run_id = ? ORDER BY source_row_number ASC",
        (run_id,),
    ):
        bank_entry_id = str(row["bank_entry_id"])
        linked = ("BANK_ENTRY", bank_entry_id) in matched_records
        _count("BANK_ENTRY", linked)
        records.append(
            _base(
                "BANK_ENTRY",
                bank_entry_id,
                amount_paise=int(row["signed_amount_paise"]),
                occurred_at_utc=str(row["posted_at_utc"]),
                content_hash=str(row["content_hash"]) if row["content_hash"] else None,
                source_row_number=int(row["source_row_number"]),
                linked=linked,
                missing_links=[] if linked else ["NO_MATCH_GROUP"],
                counterparties={
                    "utr": str(row["utr"]) if row["utr"] else None,
                    "value_date": str(row["value_date"]),
                    # narration is synthetic free text; account_fingerprint is a
                    # digest, never a real account number.
                    "narration": str(row["narration"]) if row["narration"] else None,
                    "account_fingerprint": (
                        str(row["account_fingerprint"]) if row["account_fingerprint"] else None
                    ),
                },
            )
        )

    for row in db.query_all(
        "SELECT * FROM norm_ledger_entries WHERE run_id = ? ORDER BY source_row_number ASC",
        (run_id,),
    ):
        ledger_entry_id = str(row["ledger_entry_id"])
        linked = ("LEDGER_ENTRY", ledger_entry_id) in matched_records
        _count("LEDGER_ENTRY", linked)
        records.append(
            _base(
                "LEDGER_ENTRY",
                ledger_entry_id,
                amount_paise=int(row["signed_amount_paise"]),
                occurred_at_utc=str(row["accounting_date"]),
                content_hash=str(row["content_hash"]) if row["content_hash"] else None,
                source_row_number=int(row["source_row_number"]),
                linked=linked,
                missing_links=[] if linked else ["NO_MATCH_GROUP"],
                counterparties={
                    "account_code": str(row["account_code"]),
                    "source_reference": (
                        str(row["source_reference"]) if row["source_reference"] else None
                    ),
                    "source_type": str(row["source_type"]) if row["source_type"] else None,
                    "entry_origin": str(row["entry_origin"]) if row["entry_origin"] else None,
                    "description": str(row["description"]) if row["description"] else None,
                },
            )
        )

    for kind in _MATRIX_RECORD_TYPES:
        census.setdefault(kind, {"total": 0, "reconciled": 0, "unmatched": 0})

    filtered = records
    if requested_type != "ALL":
        filtered = [item for item in filtered if item["record_type"] == requested_type]
    if requested_link_state != "ALL":
        filtered = [item for item in filtered if item["link_state"] == requested_link_state]

    needle = search.strip().lower()
    if needle:
        filtered = [item for item in filtered if needle in _matrix_search_corpus(item)]

    total_records = len(filtered)
    total_pages = max(1, (total_records + limit - 1) // limit)
    start_index = (page - 1) * limit

    inventory_total = sum(bucket["total"] for bucket in census.values())
    inventory_reconciled = sum(bucket["reconciled"] for bucket in census.values())

    return {
        "run_id": run_id,
        "total": total_records,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "record_type": requested_type,
        "link_state": requested_link_state,
        "search": search.strip(),
        "inventory": {
            "total_records": inventory_total,
            "reconciled_records": inventory_reconciled,
            "unmatched_records": inventory_total - inventory_reconciled,
            "by_record_type": census,
        },
        "records": filtered[start_index : start_index + limit],
    }


@router.get("/{run_id}/audit")
def get_run_audit(run_id: str, request: Request) -> list[dict[str, Any]]:
    """Return this run's append-only audit records in authoritative order.

    Fails closed on an unknown run so an empty list always means "this run
    recorded no events", never "that run does not exist". Each event carries
    its storage ``sequence`` so a client renders and asserts the real append
    order rather than trusting wall-clock timestamps that can tie.
    """
    db: Database = request.app.state.db
    _require_run(db, run_id)
    return [event.to_dict() for event in get_audit_trail(db=db, run_id=run_id)]
