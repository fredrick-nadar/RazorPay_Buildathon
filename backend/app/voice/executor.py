"""Safe voice action executor (PRD 13.5.2 tool boundary).

Every executor is READ-ONLY against financial tables. There is no
model-callable or voice-callable approve, apply, update_ledger, or
mark_resolved action anywhere in this module - the only writes in the
entire voice layer are append-only audit events, emitted by the service.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.domain.money import format_paise, require_paise
from app.persistence.database import Database
from app.voice.enums import VoiceIntent, VoiceLanguage, VoiceRequestStatus
from app.voice.schemas import (
    VoiceCaseCard,
    VoiceEntity,
    VoiceExecutionResult,
    VoicePreviewCard,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PRESENTATION_ROUTE = "/presentation"

_CASE_COLUMNS = (
    "case_id, run_id, category_candidate, status, variance_paise, "
    "affected_amount_paise, proposed_delta_paise, currency, summary"
)


def _case_card(row: sqlite3.Row | dict[str, Any]) -> VoiceCaseCard:
    return VoiceCaseCard(
        case_id=str(row["case_id"]),
        category=str(row["category_candidate"]),
        status=str(row["status"]),
        variance_paise=int(row["variance_paise"]),
        currency=str(row["currency"]),
        summary=str(row["summary"])[:220],
    )


def _fetch_case(db: Database, case_id: str) -> sqlite3.Row | None:
    return db.query_one(f"SELECT {_CASE_COLUMNS} FROM cases WHERE case_id = ?", (case_id,))


def _show_case(db: Database, entity: VoiceEntity, language: VoiceLanguage) -> VoiceExecutionResult:
    if entity.case_id is None:
        return VoiceExecutionResult(
            status=VoiceRequestStatus.NOT_UNDERSTOOD,
            intent=VoiceIntent.SHOW_CASE,
            message_key="case_reference_missing",
            message="Which case? Repeat the full case ID, for example: show case c9aa7339d62d.",
            language=language,
        )
    row = _fetch_case(db, entity.case_id)
    if row is None:
        return VoiceExecutionResult(
            status=VoiceRequestStatus.NOT_FOUND,
            intent=VoiceIntent.SHOW_CASE,
            message_key="case_not_found",
            message=(
                f"No case matches {entity.case_id}. Case IDs use a "
                "12-character code; copy it from the case list."
            ),
            language=language,
        )
    card = _case_card(row)
    return VoiceExecutionResult(
        status=VoiceRequestStatus.EXECUTED,
        intent=VoiceIntent.SHOW_CASE,
        message_key="case_shown",
        message=(
            f"Opening {card.case_id} - "
            f"{card.category.replace('_', ' ').lower()}, "
            f"status {card.status.replace('_', ' ').lower()}."
        ),
        language=language,
        cases=[card],
        navigation={"type": "select_case", "case_id": card.case_id},
    )


def _explain_case(
    db: Database, entity: VoiceEntity, language: VoiceLanguage
) -> VoiceExecutionResult:
    if entity.case_id is None:
        unresolved = db.query_all(
            f"SELECT {_CASE_COLUMNS} FROM cases WHERE status = 'UNRESOLVED' "
            "ORDER BY rowid DESC LIMIT 5"
        )
        cards = [_case_card(row) for row in unresolved]
        return VoiceExecutionResult(
            status=VoiceRequestStatus.EXECUTED,
            intent=VoiceIntent.EXPLAIN_CASE,
            message_key="briefing_unresolved",
            message=(
                f"{len(cards)} unresolved cases. "
                + "; ".join(f"{c.case_id} {c.category.replace('_', ' ').lower()}" for c in cards)
                if cards
                else (
                    "No unresolved cases. Every discrepancy has a verified "
                    "proof or an explicit resolution."
                )
            ),
            language=language,
            cases=cards,
        )
    row = _fetch_case(db, entity.case_id)
    if row is None:
        return VoiceExecutionResult(
            status=VoiceRequestStatus.NOT_FOUND,
            intent=VoiceIntent.EXPLAIN_CASE,
            message_key="case_not_found",
            message=f"No case matches {entity.case_id}.",
            language=language,
        )
    card = _case_card(row)
    proof = db.query_one(
        "SELECT verifier_status, authority_decision, competing_candidates_json, "
        "uncertainty_json FROM proofs WHERE case_id = ? ORDER BY rowid DESC LIMIT 1",
        (entity.case_id,),
    )
    reasons_raw = db.query_one(
        "SELECT reason_codes_json FROM cases WHERE case_id = ?", (entity.case_id,)
    )
    import json as _json

    reasons = _json.loads(str(reasons_raw["reason_codes_json"])) if reasons_raw else []
    lines = [
        f"{card.case_id}: {card.summary}",
        f"Status {card.status.replace('_', ' ').lower()}, variance {card.variance_paise} paise.",
    ]
    if proof is not None:
        lines.append(
            f"Verifier {str(proof['verifier_status']).lower()} with "
            f"{str(proof['authority_decision']).replace('_', ' ').lower()}."
        )
        candidates = _json.loads(str(proof["competing_candidates_json"] or "[]"))
        if candidates:
            lines.append(
                f"{len(candidates)} competing candidates remain; a unique discriminator is missing."
            )
    if reasons:
        lines.append("Reason codes: " + ", ".join(str(code) for code in reasons).lower() + ".")
    briefing = " ".join(lines)
    return VoiceExecutionResult(
        status=VoiceRequestStatus.EXECUTED,
        intent=VoiceIntent.EXPLAIN_CASE,
        message_key="case_explained",
        message=briefing,
        language=language,
        cases=[card],
        briefing=briefing,
        navigation={"type": "select_case", "case_id": card.case_id},
    )


def _list_unresolved(db: Database, language: VoiceLanguage) -> VoiceExecutionResult:
    rows = db.query_all(
        f"SELECT {_CASE_COLUMNS} FROM cases WHERE status = 'UNRESOLVED' "
        "ORDER BY abs(variance_paise) DESC LIMIT 10"
    )
    cards = [_case_card(row) for row in rows]
    message = (
        f"{len(cards)} unresolved case(s), highest variance first."
        if cards
        else "No unresolved cases right now."
    )
    return VoiceExecutionResult(
        status=VoiceRequestStatus.EXECUTED,
        intent=VoiceIntent.LIST_UNRESOLVED_CASES,
        message_key="unresolved_listed",
        message=message,
        language=language,
        cases=cards,
        navigation={"type": "filter_cases", "status": "UNRESOLVED"},
    )


def _filter_cases(
    db: Database, entity: VoiceEntity, language: VoiceLanguage
) -> VoiceExecutionResult:
    sql = f"SELECT {_CASE_COLUMNS} FROM cases WHERE 1=1"
    params: list[Any] = []
    if entity.status:
        sql += " AND status = ?"
        params.append(entity.status)
    if entity.category:
        sql += " AND category_candidate = ?"
        params.append(entity.category)
    if entity.amount_paise is not None:
        sql += " AND proposed_delta_paise IS NOT NULL AND abs(proposed_delta_paise) <= ?"
        params.append(entity.amount_paise)
    sql += " ORDER BY rowid DESC LIMIT 10"
    rows = db.query_all(sql, tuple(params))
    cards = [_case_card(row) for row in rows]
    filters = []
    if entity.status:
        filters.append(entity.status.replace("_", " ").lower())
    if entity.category:
        filters.append(entity.category.replace("_", " ").lower())
    if entity.amount_paise is not None:
        filters.append(f"delta within {entity.amount_paise} paise")
    detail = ", ".join(filters) if filters else "the selected filter"
    return VoiceExecutionResult(
        status=VoiceRequestStatus.EXECUTED,
        intent=VoiceIntent.FILTER_CASES,
        message_key="cases_filtered",
        message=f"{len(cards)} case(s) for {detail}.",
        language=language,
        cases=cards,
        navigation={
            "type": "filter_cases",
            "status": entity.status,
            "category": entity.category,
            "max_amount_paise": entity.amount_paise,
        },
    )


def _missing_evidence(
    db: Database, entity: VoiceEntity, language: VoiceLanguage
) -> VoiceExecutionResult:
    if entity.case_id is not None:
        row = _fetch_case(db, entity.case_id)
        if row is None:
            return VoiceExecutionResult(
                status=VoiceRequestStatus.NOT_FOUND,
                intent=VoiceIntent.SHOW_MISSING_EVIDENCE,
                message_key="case_not_found",
                message=f"No case matches {entity.case_id}.",
                language=language,
            )
        import json as _json

        reasons_row = db.query_one(
            "SELECT reason_codes_json FROM cases WHERE case_id = ?", (entity.case_id,)
        )
        reasons = _json.loads(str(reasons_row["reason_codes_json"])) if reasons_row else []
        evidence = db.query_all(
            "SELECT record_type, record_id FROM case_evidence WHERE case_id = ?",
            (entity.case_id,),
        )
        missing = (
            ", ".join(str(code) for code in reasons).lower() or "no explicit reason code recorded"
        )
        message = (
            f"{entity.case_id}: missing discriminator - {missing}. "
            f"{len(evidence)} evidence records are attached."
        )
        return VoiceExecutionResult(
            status=VoiceRequestStatus.EXECUTED,
            intent=VoiceIntent.SHOW_MISSING_EVIDENCE,
            message_key="missing_evidence",
            message=message,
            language=language,
            briefing=message,
            navigation={"type": "select_case", "case_id": entity.case_id},
        )
    return _list_unresolved(db, language)


def _prepare_previews(
    db: Database, entity: VoiceEntity, language: VoiceLanguage
) -> VoiceExecutionResult:
    sql = (
        "SELECT c.case_id, c.correction_id, c.proposed_delta_paise, "
        "c.variance_before_paise, c.variance_after_paise, c.status "
        "FROM corrections c JOIN cases k ON k.case_id = c.case_id "
        "WHERE k.status = 'APPROVAL_REQUIRED' AND c.status = 'DRAFT'"
    )
    params: list[Any] = []
    if entity.amount_paise is not None:
        sql += " AND abs(c.proposed_delta_paise) <= ?"
        params.append(entity.amount_paise)
    sql += " ORDER BY c.rowid DESC LIMIT 10"
    rows = db.query_all(sql, tuple(params))
    previews = [
        VoicePreviewCard(
            case_id=str(row["case_id"]),
            correction_id=str(row["correction_id"]),
            proposed_delta_paise=int(row["proposed_delta_paise"]),
            variance_before_paise=int(row["variance_before_paise"]),
            variance_after_paise=int(row["variance_after_paise"]),
            status=str(row["status"]),
        )
        for row in rows
    ]
    message = (
        f"{len(previews)} verified correction preview(s) ready in the approval panel. "
        "Nothing is applied until you approve in the UI."
        if previews
        else "No verified corrections are waiting for approval."
    )
    return VoiceExecutionResult(
        status=VoiceRequestStatus.EXECUTED,
        intent=VoiceIntent.PREPARE_VERIFIED_CORRECTION_PREVIEWS,
        message_key="previews_listed",
        message=message,
        language=language,
        previews=previews,
        navigation={"type": "filter_cases", "status": "APPROVAL_REQUIRED"},
    )


def _run_reconciliation(db: Database, language: VoiceLanguage) -> VoiceExecutionResult:
    from app.runs import execute_run

    result = execute_run(
        inputs_dir=REPO_ROOT / "datasets" / "dev" / "inputs",
        database=db,
        mode="rules-only",
    )
    summary = result.summary or {}
    return VoiceExecutionResult(
        status=VoiceRequestStatus.EXECUTED,
        intent=VoiceIntent.RUN_RECONCILIATION,
        message_key="run_completed",
        message=(
            f"Batch {result.run_id} completed: "
            f"{summary.get('eligible_record_count', 0)} records, "
            f"{summary.get('cases_count', 0)} exception cases."
        ),
        language=language,
        run={
            "run_id": result.run_id,
            "reused": result.reused,
            "economic_output_hash": result.economic_output_hash,
        },
        navigation={"type": "refresh_runs", "run_id": result.run_id},
    )


def execute_intent(
    db: Database,
    intent: VoiceIntent,
    entity: VoiceEntity,
    language: VoiceLanguage,
) -> VoiceExecutionResult:
    """Dispatch one allowed intent. Read-only except audit (written by service)."""
    if intent is VoiceIntent.SHOW_CASE:
        return _show_case(db, entity, language)
    if intent is VoiceIntent.EXPLAIN_CASE:
        return _explain_case(db, entity, language)
    if intent is VoiceIntent.LIST_UNRESOLVED_CASES:
        return _list_unresolved(db, language)
    if intent is VoiceIntent.FILTER_CASES:
        return _filter_cases(db, entity, language)
    if intent is VoiceIntent.SHOW_MISSING_EVIDENCE:
        return _missing_evidence(db, entity, language)
    if intent is VoiceIntent.PREPARE_VERIFIED_CORRECTION_PREVIEWS:
        return _prepare_previews(db, entity, language)
    if intent is VoiceIntent.RUN_RECONCILIATION:
        return _run_reconciliation(db, language)
    if intent is VoiceIntent.OPEN_PRESENTATION_MODE:
        return VoiceExecutionResult(
            status=VoiceRequestStatus.EXECUTED,
            intent=VoiceIntent.OPEN_PRESENTATION_MODE,
            message_key="presentation_opened",
            message="Opening presentation mode.",
            language=language,
            navigation={"type": "navigate", "route": PRESENTATION_ROUTE},
        )
    if intent is VoiceIntent.BRIEF_STATUS:
        return _brief_status(db, language)
    if intent is VoiceIntent.CANCEL_VOICE_REQUEST:
        return VoiceExecutionResult(
            status=VoiceRequestStatus.EXECUTED,
            intent=VoiceIntent.CANCEL_VOICE_REQUEST,
            message_key="cancelled",
            message="Voice request cancelled.",
            language=language,
        )
    return VoiceExecutionResult(
        status=VoiceRequestStatus.ERROR,
        message_key="unknown_intent",
        message="Intent could not be dispatched.",
        language=language,
    )


__all__ = ["PRESENTATION_ROUTE", "execute_intent"]


def _brief_status(db: Database, language: VoiceLanguage) -> VoiceExecutionResult:
    """Deterministic answers to natural batch questions - computed, never generated."""
    latest = db.query_one(
        "SELECT run_id, status, summary_json FROM runs ORDER BY rowid DESC LIMIT 1"
    )
    if latest is None:
        return VoiceExecutionResult(
            status=VoiceRequestStatus.NOT_FOUND,
            intent=VoiceIntent.BRIEF_STATUS,
            message_key="no_runs",
            message="No batch has run yet. Say: run reconciliation, to start one.",
            language=language,
        )
    import json as _json

    summary = _json.loads(str(latest["summary_json"]))
    eligible = int(summary.get("eligible_record_count", 0))
    matched = int(summary.get("matched_record_count", 0))
    cases = summary.get("cases", [])
    rate_block = summary.get("runtime_match_rate", {})
    rate = (
        f"{(100 * rate_block.get('numerator', 0) / rate_block.get('denominator', 1)):.1f}%"
        if rate_block.get("denominator")
        else "\u2014"
    )
    unresolved = sum(1 for c in cases if c.get("status") == "UNRESOLVED")
    approval = sum(1 for c in cases if c.get("status") == "APPROVAL_REQUIRED")
    totals = summary.get("financial_control_totals", {})
    variance = int(totals.get("residual_abs_variance_paise", 0))
    variance_text = format_paise(require_paise(variance))
    lines = [
        f"Latest batch: {eligible} records, {matched} matched, match rate {rate}.",
        f"{len(cases)} exception cases: {approval} awaiting approval, {unresolved} unresolved.",
        f"Residual variance {variance_text}.",
    ]
    message = " ".join(lines)
    return VoiceExecutionResult(
        status=VoiceRequestStatus.EXECUTED,
        intent=VoiceIntent.BRIEF_STATUS,
        message_key="conversational_answer",
        message=message,
        language=language,
        briefing=message,
        navigation={"type": "refresh_runs"},
    )
