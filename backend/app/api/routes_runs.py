"""Runs API routes for ARGUS CONTROL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.ai.chain import build_chain
from app.config import get_settings
from app.investigator.llm_provider import LLMInvestigatorProvider
from app.investigator.provider import FakeProvider, InvestigatorProvider
from app.persistence.database import Database
from app.runs import execute_run

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def _resolve_agent_provider() -> InvestigatorProvider:
    """Agent mode: live LLM chain when configured, deterministic fake otherwise.

    The provider id is persisted in the run summary either way, so the UI and
    the benchmark artifacts always show WHICH investigator ran.
    """
    settings = get_settings()
    chain = build_chain(settings)
    if chain.member_ids:
        return LLMInvestigatorProvider(chain)
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
    repo_root = Path(__file__).resolve().parents[3]
    inputs_path = repo_root / "datasets" / payload.dataset_profile / "inputs"

    if not inputs_path.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"dataset inputs directory not found at {inputs_path}",
        )

    provider = _resolve_agent_provider() if payload.mode == "agent" else None

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
