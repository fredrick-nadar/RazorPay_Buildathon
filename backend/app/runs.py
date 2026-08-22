"""Rules-only run orchestration (PRD 7.1, 8.4).

``execute_run`` ingests a dataset ``inputs`` directory, reconciles it
deterministically, materializes cases and the evidence graph, persists every
result through the SQLite boundary, and returns the PRD 8.4 output contract.
There is no model dependency anywhere on this path.

Idempotency: the run id is derived from the inputs fingerprint, the
normalizer version, and the rule manifest version. Re-running the same
inputs with the same code returns the stored completed run unchanged.
``force=True`` replaces a stored completed run failure-safely: the
replacement is fully computed first (pure, no writes), then the delete plus
the complete re-insert happen inside ONE transaction, so a failed forced
recomputation - before or during the swap - rolls back and retains the
previous completed result untouched. The economic output hash covers
matches, cases, quarantine, duplicates, totals, and counts - never row
numbers, ordering, timestamps, or surrogate ids - so reruns and row
reorderings are byte-verifiable.

Failure semantics: a migration failure happens in ``Database`` construction,
before any runs table exists, and surfaces as PersistenceMigrationError with
no run row (see persistence.migrations). Failures after the run row is
created persist status FAILED; failures during a forced replacement never
disturb the previous completed run.
"""

from __future__ import annotations

import json
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.domain.enums import BatchStatus
from app.graph.evidence import build_evidence_graph
from app.importers.ingest import IngestResult, ingest_inputs
from app.persistence.database import Database
from app.reconciliation.detectors import ReconciliationResult, reconcile
from app.reconciliation.rules import rule_manifest
from app.reconciliation.totals import control_totals, verify_match_invariants

NORMALIZER_VERSION = "normalizer-v1"
RUN_KEY_VERSION = "run-v1"
DEFAULT_TENANT = "argus-demo"


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: BatchStatus
    reused: bool
    idempotency_key: str
    economic_output_hash: str
    summary: dict[str, Any]


def compute_idempotency_key(inputs_fingerprint: str) -> str:
    material = "|".join(
        (
            RUN_KEY_VERSION,
            DEFAULT_TENANT,
            inputs_fingerprint,
            NORMALIZER_VERSION,
            _rule_manifest_fingerprint(),
        )
    )
    return sha256(material.encode("utf-8")).hexdigest()


def _rule_manifest_fingerprint() -> str:
    return sha256(
        json.dumps(rule_manifest(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def economic_output_hash(
    ingest: IngestResult, result: ReconciliationResult, totals: dict[str, Any]
) -> str:
    canonical = {
        "counts": {
            "raw_rows": ingest.raw_row_count,
            "accepted": ingest.accepted_count,
            "quarantined": ingest.quarantined_count,
            "duplicate_deliveries": ingest.duplicate_delivery_count,
            "matched_records": result.matched_record_count,
        },
        "totals": totals,
        "matches": sorted(
            [
                [
                    group.relationship_type.value,
                    group.rule_id,
                    group.amount_paise,
                    sorted(
                        [
                            member.record_type,
                            member.record_id,
                            member.signed_contribution_paise,
                        ]
                        for member in group.members
                    ),
                ]
                for group in result.matches
            ]
        ),
        "cases": sorted(
            [
                [
                    case.category.value,
                    case.variance_paise,
                    case.affected_amount_paise,
                    case.variance_scope,
                    sorted(case.reason_codes),
                    sorted([item.record_type, item.record_id] for item in case.evidence),
                ]
                for case in result.cases
            ]
        ),
        "quarantine": sorted(
            [
                row.source_type.value,
                row.source_record_id,
                row.quarantine_reason.value if row.quarantine_reason else "",
            ]
            for row in ingest.rows
            if row.state == "QUARANTINED"
        ),
        "duplicates": sorted(
            [
                row.source_type.value,
                row.source_record_id,
            ]
            for row in ingest.rows
            if row.state == "DUPLICATE_DELIVERY"
        ),
    }
    return sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def find_run(database: Database, run_id: str) -> dict[str, Any] | None:
    row = database.query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "status": row["status"],
        "economic_output_hash": row["economic_output_hash"],
        "summary": json.loads(row["summary_json"]),
    }


def delete_run_rows(database: Database, run_id: str) -> None:
    case_ids = [
        row["case_id"]
        for row in database.query_all("SELECT case_id FROM cases WHERE run_id = ?", (run_id,))
    ]
    match_ids = [
        row["match_id"]
        for row in database.query_all(
            "SELECT match_id FROM match_groups WHERE run_id = ?", (run_id,)
        )
    ]
    for match_id in match_ids:
        database.execute("DELETE FROM match_members WHERE match_id = ?", (match_id,))
    for case_id in case_ids:
        database.execute("DELETE FROM case_evidence WHERE case_id = ?", (case_id,))
    database.execute("DELETE FROM match_groups WHERE run_id = ?", (run_id,))
    database.execute("DELETE FROM cases WHERE run_id = ?", (run_id,))
    for table in (
        "norm_payments",
        "norm_refunds",
        "norm_settlements",
        "norm_bank_entries",
        "norm_ledger_entries",
        "source_rows",
    ):
        database.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
    database.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


def _persist_ingest(database: Database, run_id: str, ingest: IngestResult) -> None:
    database.execute_many(
        "INSERT INTO source_rows (run_id, source_type, source_row_number, source_file,"
        " source_record_id, content_hash, raw_payload_json, state,"
        " quarantine_reason, quarantine_detail, duplicate_of_row_number)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                run_id,
                row.source_type.value,
                row.source_row_number,
                row.source_file,
                row.source_record_id,
                row.content_hash,
                row.raw_payload_json,
                row.state,
                row.quarantine_reason.value if row.quarantine_reason else None,
                row.quarantine_detail,
                row.duplicate_of_row_number,
            )
            for row in ingest.rows
        ],
    )
    records = ingest.records
    database.execute_many(
        "INSERT INTO norm_payments (run_id, payment_id, source_row_number,"
        " content_hash, order_id, status, currency, gross_amount_paise, fee_paise,"
        " tax_paise, captured_at_utc, settlement_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                run_id,
                payment.payment_id,
                payment.provenance.source_row_number,
                payment.provenance.content_hash,
                payment.order_id,
                payment.status,
                payment.currency,
                int(payment.gross_amount_paise),
                int(payment.fee_paise),
                int(payment.tax_paise),
                _iso(payment.captured_at_utc),
                payment.settlement_id,
            )
            for payment in records.payments
        ],
    )
    database.execute_many(
        "INSERT INTO norm_refunds (run_id, refund_id, source_row_number,"
        " content_hash, payment_id, status, currency, refund_amount_paise,"
        " created_at_utc, settlement_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                run_id,
                refund.refund_id,
                refund.provenance.source_row_number,
                refund.provenance.content_hash,
                refund.payment_id,
                refund.status,
                refund.currency,
                int(refund.refund_amount_paise),
                _iso(refund.created_at_utc),
                refund.settlement_id,
            )
            for refund in records.refunds
        ],
    )
    database.execute_many(
        "INSERT INTO norm_settlements (run_id, settlement_id, source_row_number,"
        " content_hash, settled_at_utc, window_start_utc, window_end_utc, status,"
        " currency, gross_credit_paise, fee_paise, tax_paise, adjustment_paise,"
        " net_amount_paise, utr) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                run_id,
                settlement.settlement_id,
                settlement.provenance.source_row_number,
                settlement.provenance.content_hash,
                _iso(settlement.settled_at_utc),
                _iso(settlement.window_start_utc),
                _iso(settlement.window_end_utc),
                settlement.status,
                settlement.currency,
                int(settlement.gross_credit_paise),
                int(settlement.fee_paise),
                int(settlement.tax_paise),
                int(settlement.adjustment_paise),
                int(settlement.net_amount_paise),
                settlement.utr,
            )
            for settlement in records.settlements
        ],
    )
    database.execute_many(
        "INSERT INTO norm_bank_entries (run_id, bank_entry_id, source_row_number,"
        " content_hash, posted_at_utc, value_date, currency, signed_amount_paise,"
        " narration, utr, account_fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                run_id,
                credit.bank_entry_id,
                credit.provenance.source_row_number,
                credit.provenance.content_hash,
                _iso(credit.posted_at_utc),
                credit.value_date.isoformat(),
                credit.currency,
                int(credit.signed_amount_paise),
                credit.narration,
                credit.utr,
                credit.account_fingerprint,
            )
            for credit in records.bank_entries
        ],
    )
    database.execute_many(
        "INSERT INTO norm_ledger_entries (run_id, ledger_entry_id, source_row_number,"
        " content_hash, account_code, accounting_date, currency, signed_amount_paise,"
        " source_reference, source_type, description, entry_origin)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                run_id,
                entry.ledger_entry_id,
                entry.provenance.source_row_number,
                entry.provenance.content_hash,
                entry.account_code,
                entry.accounting_date.isoformat(),
                entry.currency,
                int(entry.signed_amount_paise),
                entry.source_reference,
                entry.source_type,
                entry.description,
                entry.entry_origin,
            )
            for entry in records.ledger_entries
        ],
    )


def _persist_reconciliation(
    database: Database,
    run_id: str,
    result: ReconciliationResult,
    created_at: str,
) -> None:
    for group in result.matches:
        database.execute(
            "INSERT INTO match_groups (match_id, run_id, relationship_type, rule_id,"
            " rule_version, status, amount_paise, created_at_utc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                group.match_id,
                run_id,
                group.relationship_type.value,
                group.rule_id,
                group.rule_version,
                "MATCHED",
                group.amount_paise,
                created_at,
            ),
        )
        database.execute_many(
            "INSERT INTO match_members (match_id, record_type, record_id, role,"
            " signed_contribution_paise) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    group.match_id,
                    member.record_type,
                    member.record_id,
                    member.role,
                    member.signed_contribution_paise,
                )
                for member in group.members
            ],
        )
    for case in result.cases:
        database.execute(
            "INSERT INTO cases (case_id, run_id, category_candidate, status,"
            " variance_paise, affected_amount_paise, proposed_delta_paise, currency,"
            " summary, reason_codes_json, opened_at_utc, updated_at_utc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case.case_id,
                run_id,
                case.category.value,
                case.status.value,
                case.variance_paise,
                case.affected_amount_paise,
                None,
                case.currency,
                case.summary,
                json.dumps(list(case.reason_codes)),
                created_at,
                created_at,
            ),
        )
        database.execute_many(
            "INSERT INTO case_evidence (case_id, record_type, record_id, note) VALUES (?, ?, ?, ?)",
            [(case.case_id, item.record_type, item.record_id, None) for item in case.evidence],
        )


def _runtime_output(
    ingest: IngestResult,
    result: ReconciliationResult,
    totals: dict[str, Any],
    graph_json: dict[str, Any],
    econ_hash: str,
    timings: dict[str, Any],
) -> dict[str, Any]:
    cases_by_category: dict[str, int] = {}
    for case in result.cases:
        cases_by_category[case.category.value] = cases_by_category.get(case.category.value, 0) + 1
    return {
        "batch_status": BatchStatus.COMPLETED.value,
        "normalizer_version": NORMALIZER_VERSION,
        "eligible_record_count": ingest.accepted_count,
        "matched_record_count": result.matched_record_count,
        "runtime_match_rate": {
            "numerator": result.matched_record_count,
            "denominator": ingest.accepted_count,
            "note": "runtime self-report; the evaluator reports frozen metrics",
        },
        "match_groups_count": len(result.matches),
        "cases_count": len(result.cases),
        "cases_by_category": dict(sorted(cases_by_category.items())),
        "raw_row_count": ingest.raw_row_count,
        "quarantined_row_count": ingest.quarantined_count,
        "duplicate_delivery_count": ingest.duplicate_delivery_count,
        "row_accounting": {
            "raw": ingest.raw_row_count,
            "accepted": ingest.accepted_count,
            "quarantined": ingest.quarantined_count,
            "duplicate_delivery": ingest.duplicate_delivery_count,
            "identity_holds": (
                ingest.accepted_count + ingest.quarantined_count + ingest.duplicate_delivery_count
                == ingest.raw_row_count
            ),
        },
        "file_stats": [
            {
                "file": stat.file_stem,
                "raw_rows": stat.raw_rows,
                "accepted": stat.accepted,
                "quarantined": stat.quarantined,
                "duplicate_delivery": stat.duplicate_delivery,
            }
            for stat in ingest.file_stats
        ],
        "quarantined_rows": [
            {
                "source_type": row.source_type.value,
                "source_row_number": row.source_row_number,
                "source_record_id": row.source_record_id,
                "reason": row.quarantine_reason.value if row.quarantine_reason else None,
                "detail": row.quarantine_detail,
            }
            for row in sorted(ingest.rows, key=lambda row: row.source_row_number)
            if row.state == "QUARANTINED"
        ],
        "financial_control_totals": totals,
        "timing_metrics": timings,
        "rule_version_manifest": rule_manifest(),
        "graph": graph_json,
        "economic_output_hash": econ_hash,
        "match_invariant_violations": verify_match_invariants(list(result.matches)),
        "unaccounted_record_keys": sorted(
            f"{key[0]}:{key[1]}" for key in result.unaccounted_record_keys
        ),
        "matches": [
            {
                "match_id": group.match_id,
                "relationship_type": group.relationship_type.value,
                "rule_id": group.rule_id,
                "amount_paise": group.amount_paise,
                "members": [
                    {
                        "record_type": member.record_type,
                        "record_id": member.record_id,
                        "role": member.role,
                        "signed_contribution_paise": member.signed_contribution_paise,
                    }
                    for member in group.members
                ],
            }
            for group in result.matches
        ],
        "cases": [
            {
                "case_id": case.case_id,
                "category": case.category.value,
                "variance_paise": case.variance_paise,
                "affected_amount_paise": case.affected_amount_paise,
                "proposed_delta_paise": None,
                "variance_scope": case.variance_scope,
                "reason_codes": list(case.reason_codes),
                "evidence": [
                    {"record_type": item.record_type, "record_id": item.record_id}
                    for item in case.evidence
                ],
            }
            for case in result.cases
        ],
    }


def _compute_run_outputs(
    ingest: IngestResult,
) -> tuple[ReconciliationResult, dict[str, Any], dict[str, Any], str]:
    """Pure computation shared by the normal and forced-replacement paths."""
    result = reconcile(ingest.records)
    totals = control_totals(ingest.records, list(result.cases))
    graph = build_evidence_graph(ingest.records, list(result.matches), list(result.cases))
    econ_hash = economic_output_hash(ingest, result, totals)
    return result, totals, graph.to_json(), econ_hash


def _persist_completed_run(
    database: Database,
    run_id: str,
    key: str,
    inputs_dir: Path,
    ingest: IngestResult,
    result: ReconciliationResult,
    econ_hash: str,
    summary: dict[str, Any],
) -> None:
    """Insert a full completed run (runs row plus every child row)."""
    now_iso = _iso(_utc_now())
    database.execute(
        "INSERT INTO runs (run_id, idempotency_key, tenant_id, inputs_path,"
        " inputs_fingerprint, status, economic_output_hash, rule_manifest_json,"
        " started_at_utc, finished_at_utc, summary_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            key,
            DEFAULT_TENANT,
            str(inputs_dir),
            ingest.inputs_fingerprint,
            BatchStatus.COMPLETED.value,
            econ_hash,
            json.dumps(rule_manifest(), sort_keys=True),
            now_iso,
            now_iso,
            json.dumps(summary, sort_keys=True),
        ),
    )
    _persist_ingest(database, run_id, ingest)
    _persist_reconciliation(database, run_id, result, now_iso)


def execute_run(
    inputs_dir: Path,
    database: Database,
    *,
    force: bool = False,
) -> RunResult:
    """Execute one rules-only reconciliation run end to end.

    Failure semantics:

    - Ingest failures happen before any database write and leave nothing
      behind (a migration failure raises even earlier, in ``Database``
      construction, when no runs table exists yet).
    - Failures after run creation persist status FAILED (any ``Exception``;
      ``KeyboardInterrupt``/``SystemExit`` are ``BaseException`` and are
      never swallowed).
    - ``force=True`` on a completed run is failure-safe: the replacement is
      fully computed first and swapped in one transaction, so a failed
      recomputation retains the previous completed result untouched.
    """
    inputs_dir = Path(inputs_dir)
    started_clock = time.perf_counter()
    ingest_started = started_clock

    ingest = ingest_inputs(inputs_dir)
    ingest_elapsed = time.perf_counter() - ingest_started

    key = compute_idempotency_key(ingest.inputs_fingerprint)
    run_id = f"run-{key[:16]}"
    existing = find_run(database, run_id)
    if existing is not None and existing["status"] == BatchStatus.COMPLETED.value:
        if not force:
            summary = existing["summary"]
            return RunResult(
                run_id=run_id,
                status=BatchStatus.COMPLETED,
                reused=True,
                idempotency_key=key,
                economic_output_hash=str(existing["economic_output_hash"] or ""),
                summary=summary,
            )
        # Failure-safe replacement: compute everything (pure, no writes);
        # any failure here leaves the prior completed run untouched. Then
        # swap atomically: delete + full insert inside ONE transaction, so
        # an in-transaction failure rolls back to the previous result.
        result, totals, graph_json, econ_hash = _compute_run_outputs(ingest)
        total_elapsed = time.perf_counter() - started_clock
        elapsed_s = max(total_elapsed, 0.000001)
        timings = {
            "ingest_seconds": round(ingest_elapsed, 6),
            "reconcile_seconds": round(total_elapsed - ingest_elapsed, 6),
            "total_seconds": round(total_elapsed, 6),
            "records_per_second": round(ingest.accepted_count / elapsed_s, 2),
        }
        summary = _runtime_output(ingest, result, totals, graph_json, econ_hash, timings)
        summary["run_id"] = run_id
        summary["idempotency_key"] = key
        summary["inputs_fingerprint"] = ingest.inputs_fingerprint
        with database.transaction():
            delete_run_rows(database, run_id)
            _persist_completed_run(
                database, run_id, key, inputs_dir, ingest, result, econ_hash, summary
            )
        return RunResult(
            run_id=run_id,
            status=BatchStatus.COMPLETED,
            reused=False,
            idempotency_key=key,
            economic_output_hash=econ_hash,
            summary=summary,
        )

    # No completed run exists: a leftover FAILED row from an earlier attempt
    # is cleared so the fresh attempt starts clean.
    if existing is not None:
        delete_run_rows(database, run_id)

    now = _utc_now()
    started_at = _iso(now)
    database.execute(
        "INSERT INTO runs (run_id, idempotency_key, tenant_id, inputs_path,"
        " inputs_fingerprint, status, economic_output_hash, rule_manifest_json,"
        " started_at_utc, finished_at_utc, summary_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            key,
            DEFAULT_TENANT,
            str(inputs_dir),
            ingest.inputs_fingerprint,
            BatchStatus.CREATED.value,
            None,
            json.dumps(rule_manifest(), sort_keys=True),
            started_at,
            None,
            "{}",
        ),
    )

    try:
        _update_status(database, run_id, BatchStatus.VALIDATING)
        with database.transaction():
            _persist_ingest(database, run_id, ingest)
        _update_status(database, run_id, BatchStatus.NORMALIZED)
        _update_status(database, run_id, BatchStatus.RECONCILING)
        reconcile_started = time.perf_counter()
        result, totals, graph_json, econ_hash = _compute_run_outputs(ingest)
        created_at = _iso(_utc_now())
        with database.transaction():
            _persist_reconciliation(database, run_id, result, created_at)
        reconcile_elapsed = time.perf_counter() - reconcile_started
        total_elapsed = time.perf_counter() - started_clock
        elapsed_s = max(total_elapsed, 0.000001)
        timings = {
            "ingest_seconds": round(ingest_elapsed, 6),
            "reconcile_seconds": round(reconcile_elapsed, 6),
            "total_seconds": round(total_elapsed, 6),
            "records_per_second": round(ingest.accepted_count / elapsed_s, 2),
        }
        summary = _runtime_output(ingest, result, totals, graph_json, econ_hash, timings)
        summary["run_id"] = run_id
        summary["idempotency_key"] = key
        summary["inputs_fingerprint"] = ingest.inputs_fingerprint
        finished_at = _iso(_utc_now())
        database.execute(
            "UPDATE runs SET status = ?, economic_output_hash = ?,"
            " finished_at_utc = ?, summary_json = ? WHERE run_id = ?",
            (
                BatchStatus.COMPLETED.value,
                econ_hash,
                finished_at,
                json.dumps(summary, sort_keys=True),
                run_id,
            ),
        )
        return RunResult(
            run_id=run_id,
            status=BatchStatus.COMPLETED,
            reused=False,
            idempotency_key=key,
            economic_output_hash=econ_hash,
            summary=summary,
        )
    except Exception as exc:  # noqa: BLE001 - persist FAILED, then re-raise
        # Any post-creation failure (reconciliation, graph, persistence,
        # sqlite) leaves the run FAILED instead of stuck in RECONCILING.
        # KeyboardInterrupt/SystemExit derive from BaseException and are not
        # caught; migration failures happen before any runs table exists.
        with suppress(Exception):
            database.execute(
                "UPDATE runs SET status = ?, finished_at_utc = ?, summary_json = ?"
                " WHERE run_id = ?",
                (
                    BatchStatus.FAILED.value,
                    _iso(_utc_now()),
                    json.dumps({"failure": f"{type(exc).__name__}: {exc}"}),
                    run_id,
                ),
            )
        raise


def _update_status(database: Database, run_id: str, status: BatchStatus) -> None:
    database.execute("UPDATE runs SET status = ? WHERE run_id = ?", (status.value, run_id))


def load_cases(database: Database, run_id: str) -> list[dict[str, Any]]:
    rows = database.query_all("SELECT * FROM cases WHERE run_id = ? ORDER BY case_id", (run_id,))
    cases: list[dict[str, Any]] = []
    for row in rows:
        evidence = database.query_all(
            "SELECT record_type, record_id FROM case_evidence WHERE case_id = ?"
            " ORDER BY record_type, record_id",
            (row["case_id"],),
        )
        cases.append(
            {
                "case_id": row["case_id"],
                "category": row["category_candidate"],
                "variance_paise": row["variance_paise"],
                "affected_amount_paise": row["affected_amount_paise"],
                "proposed_delta_paise": row["proposed_delta_paise"],
                "reason_codes": json.loads(row["reason_codes_json"]),
                "evidence": [
                    {"record_type": item["record_type"], "record_id": item["record_id"]}
                    for item in evidence
                ],
            }
        )
    return cases
