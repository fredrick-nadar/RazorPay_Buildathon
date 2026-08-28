"""Runs API routes for ARGUS CONTROL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.ai.chain import build_chain
from app.config import Settings
from app.investigator.llm_provider import LLMInvestigatorProvider
from app.investigator.provider import FakeProvider, InvestigatorProvider
from app.persistence.database import Database
from app.runs import execute_run

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def _resolve_agent_provider(
    settings: Settings, provider_id: str | None = None
) -> InvestigatorProvider:
    """Agent mode: live LLM chain when configured & requested, deterministic fake otherwise.

    The provider id is persisted in the run summary either way, so the UI and
    the benchmark artifacts always show WHICH investigator ran.
    """
    if not provider_id or "fake" in provider_id.lower():
        return FakeProvider()
    try:
        chain = build_chain(settings)
        if chain.member_ids:
            return LLMInvestigatorProvider(chain)
    except Exception:
        pass
    return FakeProvider()


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

    provider = (
        _resolve_agent_provider(settings, payload.provider_id) if payload.mode == "agent" else None
    )

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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("")
def list_runs(request: Request) -> list[dict[str, Any]]:
    db: Database = request.app.state.db
    rows = db.query_all(
        "SELECT run_id, tenant_id, inputs_path, status, started_at_utc, "
        "finished_at_utc, summary_json FROM runs ORDER BY rowid DESC LIMIT 50"
    )

    result = []
    for r in rows:
        summary = json.loads(str(r["summary_json"]))
        result.append(
            {
                "run_id": str(r["run_id"]),
                "tenant_id": str(r["tenant_id"]),
                "inputs_path": str(r["inputs_path"]),
                "status": str(r["status"]),
                "started_at_utc": str(r["started_at_utc"]),
                "finished_at_utc": str(r["finished_at_utc"]) if r["finished_at_utc"] else None,
                "summary": summary,
            }
        )
    return result


@router.get("/{run_id}/summary")
def get_run_summary(run_id: str, request: Request) -> dict[str, Any]:
    db: Database = request.app.state.db
    row = db.query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    summary = json.loads(str(row["summary_json"]))
    return {
        "run_id": str(row["run_id"]),
        "status": str(row["status"]),
        "economic_output_hash": str(row["economic_output_hash"])
        if row["economic_output_hash"]
        else None,
        "summary": summary,
    }


@router.get("/{run_id}/cases")
def list_run_cases(
    run_id: str,
    request: Request,
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    db: Database = request.app.state.db
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
def get_run_fee_audit(
    run_id: str,
    request: Request,
    mdr_bps: int = Query(default=200, description="Contractual MDR in basis points (200 = 2.0%)"),
    gst_bps: int = Query(
        default=1800, description="Contractual GST in basis points (1800 = 18.0%)"
    ),
) -> dict[str, Any]:
    from app.domain.fee_audit import audit_run_fees

    db: Database = request.app.state.db
    run_row = db.query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    if run_row is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")

    result = audit_run_fees(db, run_id, contractual_mdr_bps=mdr_bps, contractual_gst_bps=gst_bps)
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

    for cr in case_rows:
        cid = str(cr["case_id"])
        var_p = int(cr["variance_paise"])
        total_variance_paise += abs(var_p)

        proof_row = db.query_one(
            "SELECT * FROM proofs WHERE case_id = ? ORDER BY rowid DESC LIMIT 1", (cid,)
        )
        proof = None
        if proof_row:
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
                "status": str(cr["status"]),
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

    # Cryptographic integrity signature
    econ_hash = str(run_row["economic_output_hash"] or "none")
    raw_sig = f"argus:dossier:v1:{run_id}:{econ_hash}:{len(cases)}:{total_variance_paise}"
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
        "cryptographic_seal": crypto_hash,
        "summary": summary,
        "cases_count": len(cases),
        "total_variance_paise": total_variance_paise,
        "cases": cases,
        "audit_trail": audit_events,
        "compliance": {
            "regulator": "RBI / Merchant Settlement Standards",
            "framework": "Deterministic Flight Recorder v1.0",
            "integer_precision": "Signed Integer Paise (0 floats)",
            "immutable_source_rows": True,
            "signed_by": "Merchant Financial Controller (Automated Seal)",
        },
    }


@router.get("/{run_id}/matrix")
def get_run_matrix(
    run_id: str,
    request: Request,
    page: int = Query(default=1, ge=1, description="Page number"),
    limit: int = Query(default=50, ge=10, le=200, description="Records per page"),
    search: str = Query(default="", description="Search by ID or reference"),
) -> dict[str, Any]:
    """Return 5-Way Reconciled Master Transaction Matrix with pagination & search."""
    db: Database = request.app.state.db

    # Query all normalized payments in this run
    payments = db.query_all(
        "SELECT * FROM norm_payments WHERE run_id = ? ORDER BY source_row_number ASC",
        (run_id,),
    )
    if not payments:
        return {"run_id": run_id, "total": 0, "page": page, "limit": limit, "records": []}

    # Build fast lookup indexes for settlements, bank entries, and ledger entries in this run
    settlements = {
        str(r["settlement_id"]): r
        for r in db.query_all("SELECT * FROM norm_settlements WHERE run_id = ?", (run_id,))
    }
    bank_by_utr = {
        str(r["utr"]): r
        for r in db.query_all("SELECT * FROM norm_bank_entries WHERE run_id = ?", (run_id,))
        if r["utr"]
    }
    ledger_by_ref = {
        str(r["source_reference"]): r
        for r in db.query_all("SELECT * FROM norm_ledger_entries WHERE run_id = ?", (run_id,))
        if r["source_reference"]
    }

    # Query match group relationships
    query = (
        "SELECT mm.match_id, mm.record_id, mm.record_type, mg.rule_id, mg.relationship_type "
        "FROM match_members mm JOIN match_groups mg ON mm.match_id = mg.match_id "
        "WHERE mg.run_id = ?"
    )
    members = db.query_all(query, (run_id,))
    rule_by_record: dict[str, str] = {}
    for m in members:
        rule_by_record[str(m["record_id"])] = str(m["rule_id"])

    # Compile 5-Way Matrix Records
    all_records: list[dict[str, Any]] = []
    for p in payments:
        pay_id = str(p["payment_id"])
        order_id = str(p["order_id"]) if p["order_id"] else None
        stl_id = str(p["settlement_id"]) if p["settlement_id"] else None
        gross_p = int(p["gross_amount_paise"])
        fee_p = int(p["fee_paise"])
        tax_p = int(p["tax_paise"])
        net_p = gross_p - fee_p - tax_p

        stl_row = settlements.get(stl_id) if stl_id else None
        utr = str(stl_row["utr"]) if (stl_row and stl_row["utr"]) else None
        bank_row = bank_by_utr.get(utr) if utr else None
        led_row = ledger_by_ref.get(pay_id)

        match_rule = rule_by_record.get(pay_id) or rule_by_record.get(
            str(led_row["ledger_entry_id"]) if led_row else ""
        )

        # This endpoint is the fully linked 5-way matrix, not the unmatched
        # record inventory. Incomplete relationships remain visible in cases
        # and evidence views and must never be presented here as reconciled.
        if not (match_rule and stl_row and bank_row and led_row):
            continue

        matrix_item = {
            "payment_id": pay_id,
            "order_id": order_id,
            "gross_amount_paise": gross_p,
            "fee_paise": fee_p,
            "tax_paise": tax_p,
            "net_amount_paise": net_p,
            "captured_at_utc": str(p["captured_at_utc"]),
            "settlement_id": stl_id,
            "settlement_gross_paise": int(stl_row["gross_credit_paise"]) if stl_row else None,
            "utr": utr,
            "bank_entry_id": str(bank_row["bank_entry_id"]) if bank_row else None,
            "bank_amount_paise": int(bank_row["signed_amount_paise"]) if bank_row else None,
            "ledger_entry_id": str(led_row["ledger_entry_id"]) if led_row else None,
            "ledger_amount_paise": int(led_row["signed_amount_paise"]) if led_row else None,
            "account_code": str(led_row["account_code"]),
            "match_rule": match_rule,
            "status": "RECONCILED",
        }

        # Apply search filter if present
        search_str = search if isinstance(search, str) else ""
        if search_str:
            q = search_str.lower()
            led_id = str(matrix_item.get("ledger_entry_id") or "")
            text_corpus = f"{pay_id} {order_id} {stl_id} {utr} {led_id}".lower()
            if q not in text_corpus:
                continue

        all_records.append(matrix_item)

    total_records = len(all_records)
    total_pages = max(1, (total_records + limit - 1) // limit)
    start_idx = (page - 1) * limit
    page_records = all_records[start_idx : start_idx + limit]

    return {
        "run_id": run_id,
        "total": total_records,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "records": page_records,
    }


@router.get("/{run_id}/audit")
def get_run_audit(run_id: str, request: Request) -> list[dict[str, Any]]:
    """Return all append-only cryptographic audit records for this run."""
    db: Database = request.app.state.db
    rows = db.query_all("SELECT * FROM audit_log WHERE run_id = ? ORDER BY rowid ASC", (run_id,))
    return [
        {
            "event_id": str(r["event_id"]),
            "case_id": str(r["case_id"]) if r["case_id"] else None,
            "run_id": str(r["run_id"]) if r["run_id"] else None,
            "timestamp_utc": str(r["timestamp_utc"]),
            "actor": str(r["actor"]),
            "action": str(r["action"]),
            "payload": json.loads(str(r["payload_json"])) if r["payload_json"] else {},
            "digest": str(r["digest"]),
        }
        for r in rows
    ]
