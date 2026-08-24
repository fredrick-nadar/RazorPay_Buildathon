"""Runs API routes for ARGUS CONTROL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.investigator.provider import FakeProvider
from app.persistence.database import Database
from app.runs import execute_run

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


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

    provider = FakeProvider() if payload.mode == "agent" else None

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
