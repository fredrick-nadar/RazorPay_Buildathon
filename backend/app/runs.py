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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from app.corrections.authority import classify_authority
from app.domain.enums import BatchStatus
from app.graph.evidence import build_evidence_graph
from app.importers.ingest import IngestResult, ingest_inputs
from app.investigator.engine import investigate_cases
from app.investigator.provider import FakeProvider
from app.persistence.database import Database
from app.reconciliation.detectors import ReconciliationResult, reconcile
from app.reconciliation.rules import rule_manifest
from app.reconciliation.totals import control_totals, verify_match_invariants
from app.verifier.engine import CaseVerification, VerificationOutcome, verify_cases
from app.verifier.proof import verifier_manifest_fingerprint

NORMALIZER_VERSION = "normalizer-v1"
RUN_KEY_VERSION = "run-v2"
DEFAULT_TENANT = "argus-demo"


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: BatchStatus
    reused: bool
    idempotency_key: str
    economic_output_hash: str
    summary: dict[str, Any]


def compute_idempotency_key(
    inputs_fingerprint: str,
    mode: str = "rules-only",
    provider_id: str = "none",
) -> str:
    if mode == "agent":
        material = "|".join(
            (
                "run-v3-agent",
                DEFAULT_TENANT,
                inputs_fingerprint,
                NORMALIZER_VERSION,
                _rule_manifest_fingerprint(),
                verifier_manifest_fingerprint(),
                mode,
                provider_id,
            )
        )
    else:
        material = "|".join(
            (
                RUN_KEY_VERSION,
                DEFAULT_TENANT,
                inputs_fingerprint,
                NORMALIZER_VERSION,
                _rule_manifest_fingerprint(),
                verifier_manifest_fingerprint(),
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
                    case.status.value,
                    case.variance_paise,
                    case.affected_amount_paise,
                    case.proposed_delta_paise,
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
        database.execute("DELETE FROM corrections WHERE case_id = ?", (case_id,))
        database.execute("DELETE FROM proofs WHERE case_id = ?", (case_id,))
        database.execute("DELETE FROM hypotheses WHERE case_id = ?", (case_id,))
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
    verification: VerificationOutcome,
    created_at: str,
) -> None:
    for group in result.matches:
        database.execute(
            "INSERT OR REPLACE INTO match_groups (match_id, run_id, relationship_type, rule_id,"
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
            "INSERT OR REPLACE INTO match_members (match_id, record_type, record_id, role,"
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
            "INSERT OR REPLACE INTO cases (case_id, run_id, category_candidate, status,"
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
                case.proposed_delta_paise,
                case.currency,
                case.summary,
                json.dumps(list(case.reason_codes)),
                created_at,
                created_at,
            ),
        )
        database.execute_many(
            "INSERT OR REPLACE INTO case_evidence (case_id, record_type, record_id, note)"
            " VALUES (?, ?, ?, ?)",
            [(case.case_id, item.record_type, item.record_id, None) for item in case.evidence],
        )
    _persist_verification(database, verification, created_at)


def _persist_verification(
    database: Database, verification: VerificationOutcome, created_at: str
) -> None:
    """Persist hypotheses, proofs, and DRAFT correction previews as run outputs.

    A corrections row is a stored preview only — never an applied correction
    and never a ledger entry (approved Phase 3 clarification).
    """
    for item in verification.verifications:
        hypothesis = item.hypothesis
        database.execute(
            "INSERT OR REPLACE INTO hypotheses (hypothesis_id, case_id, category, claim,"
            " evidence_json, status, reason_codes_json, created_at_utc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                hypothesis.hypothesis_id,
                hypothesis.case_id,
                hypothesis.category.value,
                hypothesis.claim,
                json.dumps(list(hypothesis.evidence_ids), sort_keys=True),
                hypothesis.status.value,
                json.dumps(list(hypothesis.reason_codes), sort_keys=True),
                created_at,
            ),
        )
        proof = item.proof.to_json()
        database.execute(
            "INSERT OR REPLACE INTO proofs (proof_id, case_id, hypothesis_id, claim, category,"
            " evidence_json, supported_evidence_json, conflicting_evidence_json,"
            " equations_json, rejected_alternatives_json, verifier_status,"
            " verifier_rule_id, verifier_rule_version, recon_manifest_fingerprint,"
            " verifier_manifest_fingerprint, proposed_delta_paise, dry_run_json,"
            " authority_decision, requires_approval, uncertainty_json,"
            " competing_candidates_json, missing_discriminator, recommended_next_step,"
            " canonical_hash, created_at_utc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?, ?)",
            (
                proof["proof_id"],
                proof["case_id"],
                proof["hypothesis_id"],
                proof["claim"],
                proof["category"],
                json.dumps(proof["evidence_ids"], sort_keys=True),
                json.dumps(proof["supported_evidence_ids"], sort_keys=True),
                json.dumps(proof["conflicting_evidence_ids"], sort_keys=True),
                json.dumps(proof["equations"], sort_keys=True),
                json.dumps(proof["rejected_alternatives"], sort_keys=True),
                proof["verifier_status"],
                proof["verifier_rule_id"],
                proof["verifier_rule_version"],
                proof["recon_manifest_fingerprint"],
                proof["verifier_manifest_fingerprint"],
                proof["proposed_delta_paise"],
                json.dumps(proof["dry_run"], sort_keys=True) if proof["dry_run"] else None,
                proof["authority_decision"],
                1 if proof["requires_approval"] else 0,
                json.dumps(proof["uncertainty"], sort_keys=True),
                json.dumps(proof["competing_candidates"], sort_keys=True),
                proof["missing_discriminator"],
                proof["recommended_next_step"],
                proof["canonical_hash"],
                proof["created_at_utc"],
            ),
        )
        if item.dry_run is not None:
            dry = item.dry_run.to_json()
            database.execute(
                "INSERT OR REPLACE INTO corrections (correction_id, case_id, proof_id, status,"
                " proposed_entry_json, target_ledger_entry_id, account_code,"
                " proposed_delta_paise, variance_before_paise, variance_after_paise,"
                " totals_before_json, totals_after_json, warnings_json,"
                " uncertainty_json, created_at_utc)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dry["correction_id"],
                    dry["case_id"],
                    dry["proof_id"],
                    dry["status"],
                    json.dumps(dry["proposed_entry"], sort_keys=True)
                    if dry["proposed_entry"]
                    else None,
                    dry["target_ledger_entry_id"],
                    dry["account_code"],
                    dry["proposed_delta_paise"],
                    dry["variance_before_paise"],
                    dry["variance_after_paise"],
                    json.dumps(dry["totals_before_paise"], sort_keys=True),
                    json.dumps(dry["totals_after_paise"], sort_keys=True),
                    json.dumps(dry["warnings"], sort_keys=True),
                    json.dumps(dry["remaining_uncertainty"], sort_keys=True),
                    created_at,
                ),
            )


def _runtime_output(
    ingest: IngestResult,
    result: ReconciliationResult,
    totals: dict[str, Any],
    graph_json: dict[str, Any],
    econ_hash: str,
    timings: dict[str, Any],
    verification: VerificationOutcome,
    investigation_summary: dict[str, Any] | None = None,
    mode: str = "rules-only",
    provider_id: str = "none",
) -> dict[str, Any]:
    cases_by_category: dict[str, int] = {}
    for case in result.cases:
        cases_by_category[case.category.value] = cases_by_category.get(case.category.value, 0) + 1
    output: dict[str, Any] = {
        "batch_status": BatchStatus.COMPLETED.value,
        "mode": mode,
        "provider_id": provider_id,
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
        "verification": verification.summary(),
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
                "status": case.status.value,
                "variance_paise": case.variance_paise,
                "affected_amount_paise": case.affected_amount_paise,
                "proposed_delta_paise": case.proposed_delta_paise,
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
    if investigation_summary is not None:
        output["investigation"] = investigation_summary
    return output


def _compute_run_outputs(
    ingest: IngestResult,
    mode: str = "rules-only",
    provider: Any = None,
) -> tuple[
    ReconciliationResult,
    dict[str, Any],
    dict[str, Any],
    str,
    VerificationOutcome,
    dict[str, Any] | None,
]:
    """Pure computation shared by the normal and forced-replacement paths."""
    result = reconcile(ingest.records)
    verification = verify_cases(ingest.records, list(result.cases))
    investigation_summary: dict[str, Any] | None = None

    if mode == "agent":
        if provider is None:
            provider = FakeProvider()
        intermediate_graph = build_evidence_graph(
            ingest.records, list(result.matches), list(verification.cases)
        )
        inv_outcome = investigate_cases(
            records=ingest.records,
            cases=list(verification.cases),
            provider=provider,
            graph_json=intermediate_graph.to_json(),
        )
        investigation_summary = inv_outcome.summary()

        updated_verifications: list[CaseVerification] = []
        for orig_cv, inv in zip(
            verification.verifications, inv_outcome.investigations, strict=False
        ):
            if inv.status == "SKIPPED":
                updated_verifications.append(orig_cv)
            elif (
                inv.proof is not None
                and inv.hypothesis is not None
                and inv.verifier_result is not None
            ):
                auth = classify_authority(
                    inv.verifier_result.status,
                    inv.verifier_result.proposed_delta_paise,
                )
                updated_verifications.append(
                    CaseVerification(
                        case=inv.case,
                        hypothesis=inv.hypothesis,
                        result=inv.verifier_result,
                        proof=inv.proof,
                        dry_run=inv.dry_run,
                        authority=auth,
                        duration_ms=inv.duration_ms,
                    )
                )
            else:
                updated_verifications.append(replace(orig_cv, case=inv.case))

        verification = VerificationOutcome(
            verifications=tuple(updated_verifications),
            latency_ms=verification.latency_ms,
        )

    verified = ReconciliationResult(
        matches=result.matches,
        cases=verification.cases,
        matched_record_keys=result.matched_record_keys,
        case_evidence_keys=result.case_evidence_keys,
        unaccounted_record_keys=result.unaccounted_record_keys,
    )
    totals = control_totals(ingest.records, list(verified.cases))
    graph = build_evidence_graph(ingest.records, list(verified.matches), list(verified.cases))
    econ_hash = economic_output_hash(ingest, verified, totals)
    return verified, totals, graph.to_json(), econ_hash, verification, investigation_summary


def _persist_completed_run(
    database: Database,
    run_id: str,
    key: str,
    inputs_dir: Path,
    ingest: IngestResult,
    result: ReconciliationResult,
    verification: VerificationOutcome,
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
    _persist_reconciliation(database, run_id, result, verification, now_iso)

    # Append-only cryptographic audit event chain (PRD §6.12, §11.4)
    from app.audit.service import record_audit_event

    record_audit_event(
        db=database,
        actor="SYSTEM_INGEST_PIPELINE",
        action="INGEST_COMPLETED",
        payload={
            "raw_row_count": ingest.raw_row_count,
            "accepted_count": ingest.accepted_count,
            "quarantined_count": ingest.quarantined_count,
            "inputs_fingerprint": ingest.inputs_fingerprint,
            "files": [s.file_stem for s in ingest.file_stats],
        },
        run_id=run_id,
        timestamp_utc=now_iso,
    )

    denom = max(ingest.accepted_count, 1)
    rate_str = f"{(result.matched_record_count / denom * 100):.1f}%"
    record_audit_event(
        db=database,
        actor="DETERMINISTIC_RECON_ENGINE",
        action="RECONCILIATION_MATCH_COMPLETED",
        payload={
            "matched_records": result.matched_record_count,
            "match_groups_count": len(result.matches),
            "match_rate": rate_str,
            "relationship_rules_applied": list({m.rule_id for m in result.matches}),
        },
        run_id=run_id,
        timestamp_utc=now_iso,
    )

    if result.cases:
        record_audit_event(
            db=database,
            actor="AI_INVESTIGATOR_SERVICE",
            action="INVESTIGATION_DISPATCHED",
            payload={
                "exception_cases_count": len(result.cases),
                "categories": [c.category.value for c in result.cases],
                "verifications_count": len(verification.verifications),
            },
            run_id=run_id,
            timestamp_utc=now_iso,
        )

        for case in result.cases:
            record_audit_event(
                db=database,
                actor="AI_INVESTIGATOR_SERVICE",
                action="CASE_OPENED",
                payload={
                    "category": case.category.value,
                    "status": case.status.value,
                    "variance_paise": case.variance_paise,
                    "affected_amount_paise": case.affected_amount_paise,
                    "summary": case.summary,
                    "reason_codes": list(case.reason_codes),
                },
                case_id=case.case_id,
                run_id=run_id,
                timestamp_utc=now_iso,
            )

    record_audit_event(
        db=database,
        actor="FINANCIAL_CONTROLLER_SEAL",
        action="RUN_SEALED",
        payload={
            "economic_output_hash": econ_hash,
            "statutory_standard": "Signed Integer Paise (0 floats)",
            "verification_status": "SEALED",
        },
        run_id=run_id,
        timestamp_utc=now_iso,
    )


def execute_run(
    inputs_dir: Path,
    database: Database,
    *,
    force: bool = False,
    mode: Literal["rules-only", "agent"] = "rules-only",
    provider: Any = None,
) -> RunResult:
    """Execute one reconciliation run end to end (rules-only or agent).

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

    provider_obj = (
        provider if provider is not None else (FakeProvider() if mode == "agent" else None)
    )
    provider_id = (
        getattr(provider_obj, "provider_id", "none") if provider_obj is not None else "none"
    )

    key = compute_idempotency_key(ingest.inputs_fingerprint, mode=mode, provider_id=provider_id)
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
        result, totals, graph_json, econ_hash, verification, inv_summary = _compute_run_outputs(
            ingest, mode=mode, provider=provider_obj
        )
        total_elapsed = time.perf_counter() - started_clock
        elapsed_s = max(total_elapsed, 0.000001)
        timings = {
            "ingest_seconds": round(ingest_elapsed, 6),
            "reconcile_seconds": round(total_elapsed - ingest_elapsed, 6),
            "verify_seconds": round(verification.latency_ms.get("verify_total_ms", 0.0) / 1000, 6),
            "total_seconds": round(total_elapsed, 6),
            "records_per_second": round(ingest.accepted_count / elapsed_s, 2),
        }
        summary = _runtime_output(
            ingest,
            result,
            totals,
            graph_json,
            econ_hash,
            timings,
            verification,
            investigation_summary=inv_summary,
            mode=mode,
            provider_id=provider_id,
        )
        summary["run_id"] = run_id
        summary["idempotency_key"] = key
        summary["inputs_fingerprint"] = ingest.inputs_fingerprint
        with database.transaction():
            delete_run_rows(database, run_id)
            _persist_completed_run(
                database,
                run_id,
                key,
                inputs_dir,
                ingest,
                result,
                verification,
                econ_hash,
                summary,
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
        result, totals, graph_json, econ_hash, verification, inv_summary = _compute_run_outputs(
            ingest, mode=mode, provider=provider_obj
        )
        created_at = _iso(_utc_now())
        with database.transaction():
            _persist_reconciliation(database, run_id, result, verification, created_at)
        reconcile_elapsed = time.perf_counter() - reconcile_started
        total_elapsed = time.perf_counter() - started_clock
        elapsed_s = max(total_elapsed, 0.000001)
        timings = {
            "ingest_seconds": round(ingest_elapsed, 6),
            "reconcile_seconds": round(reconcile_elapsed, 6),
            "verify_seconds": round(verification.latency_ms.get("verify_total_ms", 0.0) / 1000, 6),
            "total_seconds": round(total_elapsed, 6),
            "records_per_second": round(ingest.accepted_count / elapsed_s, 2),
        }
        summary = _runtime_output(
            ingest,
            result,
            totals,
            graph_json,
            econ_hash,
            timings,
            verification,
            investigation_summary=inv_summary,
            mode=mode,
            provider_id=provider_id,
        )
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
