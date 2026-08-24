"""Read-only and exploratory-calculation tool dispatcher (PRD 10.2).

The dispatcher exposes **only** read and deterministic-calculation tools.
There are NO workflow, state-transition, verification, dry-run, approval,
application, or ledger-write tools.  The model returns data; backend engine
code owns all workflow transitions.

``calculate_*`` outputs are exploratory aids — the verifier (called by the
engine, not the model) is the sole source of authoritative financial
arithmetic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.domain.records import (
    AcceptedRecords,
    LedgerEntryRecord,
    RefundRecord,
    SettlementRecord,
)
from app.reconciliation.detectors import CaseRecord
from app.reconciliation.rules import (
    BANK_POSTING_WINDOW_S,
    REFUND_POSTING_WINDOW_DAYS,
    rule_manifest,
)
from app.reconciliation.totals import control_totals
from app.verifier.models import parse_evidence_id
from app.verifier.rules import verifier_rule_manifest
from app.verifier.snapshot import EvidenceSnapshot

# ---------------------------------------------------------------------------
# Allowlist — exactly the read + exploratory-calculation tools.
# ---------------------------------------------------------------------------

TOOL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "get_case",
        "get_evidence_graph",
        "get_record",
        "list_candidate_records",
        "get_rule_manifest",
        "calculate_control_totals",
        "calculate_expected_net",
        "check_date_window",
        "check_unique_identity",
    }
)


def _error(code: str, detail: str = "") -> dict[str, str]:
    result: dict[str, str] = {"error": code}
    if detail:
        result["detail"] = detail
    return result


def _record_to_dict(record: object) -> dict[str, Any]:
    """Serialize a frozen dataclass record to a plain dict for the model."""
    if hasattr(record, "__dataclass_fields__"):
        result: dict[str, Any] = {}
        for name in record.__dataclass_fields__:
            value = getattr(record, name)
            if hasattr(value, "__dataclass_fields__"):
                value = _record_to_dict(value)
            elif hasattr(value, "isoformat"):
                value = value.isoformat()
            result[name] = value
        return result
    return {"value": str(record)}


@dataclass
class ToolDispatcher:
    """Dispatches read-only tool calls against snapshot data.

    Budget enforcement is the caller's responsibility — the dispatcher
    itself is stateless except for its references to immutable data.
    """

    snapshot: EvidenceSnapshot
    records: AcceptedRecords
    cases: dict[str, CaseRecord]
    graph_json: dict[str, Any]

    def dispatch(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a single tool call.  Returns a result dict or an error dict."""
        if tool_name not in TOOL_ALLOWLIST:
            return _error("UNKNOWN_TOOL", f"tool {tool_name!r} is not in the allowlist")

        handler = _HANDLERS.get(tool_name)
        if handler is None:
            return _error("UNKNOWN_TOOL", f"no handler for {tool_name!r}")

        try:
            return handler(self, arguments)
        except Exception as exc:  # noqa: BLE001
            return _error("TOOL_ERROR", str(exc))


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _get_case(dispatcher: ToolDispatcher, args: dict[str, Any]) -> dict[str, Any]:
    case_id = args.get("case_id")
    if not isinstance(case_id, str):
        return _error("INVALID_ARGUMENTS", "case_id must be a string")
    case = dispatcher.cases.get(case_id)
    if case is None:
        return _error("UNKNOWN_CASE", f"case {case_id!r} not found")
    return {
        "case_id": case.case_id,
        "category": case.category.value,
        "status": case.status.value,
        "variance_paise": case.variance_paise,
        "affected_amount_paise": case.affected_amount_paise,
        "currency": case.currency,
        "summary": case.summary,
        "reason_codes": list(case.reason_codes),
        "evidence": [
            {"record_type": item.record_type, "record_id": item.record_id} for item in case.evidence
        ],
    }


def _get_evidence_graph(
    dispatcher: ToolDispatcher,
    args: dict[str, Any],
) -> dict[str, Any]:
    # Returns the full graph — case_id is accepted but not required.
    return dict(dispatcher.graph_json)


def _get_record(dispatcher: ToolDispatcher, args: dict[str, Any]) -> dict[str, Any]:
    record_id = args.get("record_id")
    if not isinstance(record_id, str):
        return _error("INVALID_ARGUMENTS", "record_id must be a string")

    parsed = parse_evidence_id(record_id)
    if parsed is None:
        return _error("UNKNOWN_EVIDENCE_ID", f"bad format: {record_id!r}")

    record_type, rid = parsed
    record = dispatcher.snapshot.record(record_type, rid)
    if record is None:
        return _error("UNKNOWN_EVIDENCE_ID", f"{record_id!r} not found")

    return _record_to_dict(record)


def _list_candidate_records(
    dispatcher: ToolDispatcher,
    args: dict[str, Any],
) -> dict[str, Any]:
    record_type = args.get("record_type")
    if not isinstance(record_type, str):
        return _error("INVALID_ARGUMENTS", "record_type must be a string")

    type_map: dict[str, dict[str, Any]] = {
        "PAYMENT": {pid: _record_to_dict(p) for pid, p in dispatcher.snapshot.payments.items()},
        "REFUND": {rid: _record_to_dict(r) for rid, r in dispatcher.snapshot.refunds.items()},
        "SETTLEMENT": {
            sid: _record_to_dict(s) for sid, s in dispatcher.snapshot.settlements.items()
        },
        "BANK_ENTRY": {
            bid: _record_to_dict(b) for bid, b in dispatcher.snapshot.bank_entries.items()
        },
        "LEDGER_ENTRY": {
            lid: _record_to_dict(le) for lid, le in dispatcher.snapshot.ledger_entries.items()
        },
    }
    candidates = type_map.get(record_type)
    if candidates is None:
        return _error("INVALID_ARGUMENTS", f"unknown record_type: {record_type!r}")
    return {"record_type": record_type, "count": len(candidates), "records": candidates}


def _get_rule_manifest(
    dispatcher: ToolDispatcher,
    args: dict[str, Any],
) -> dict[str, Any]:
    return {
        "reconciliation": rule_manifest(),
        "verification": verifier_rule_manifest(),
    }


def _calculate_control_totals(
    dispatcher: ToolDispatcher,
    args: dict[str, Any],
) -> dict[str, Any]:
    # Returns the full control totals for the run — an exploratory aid.
    totals = control_totals(dispatcher.records, list(dispatcher.cases.values()))
    return {"note": "exploratory — verifier is authoritative", "totals": totals}


def _calculate_expected_net(
    dispatcher: ToolDispatcher,
    args: dict[str, Any],
) -> dict[str, Any]:
    payment_ids = args.get("payment_ids", [])
    refund_ids = args.get("refund_ids", [])
    if not isinstance(payment_ids, list) or not isinstance(refund_ids, list):
        return _error("INVALID_ARGUMENTS", "payment_ids and refund_ids must be lists")

    gross = 0
    fee = 0
    tax = 0
    for pid in payment_ids:
        payment = dispatcher.snapshot.payments.get(pid)
        if payment is None:
            return _error("UNKNOWN_EVIDENCE_ID", f"payment {pid!r} not found")
        gross += int(payment.gross_amount_paise)
        fee += int(payment.fee_paise)
        tax += int(payment.tax_paise)

    refund_total = 0
    for rid in refund_ids:
        refund = dispatcher.snapshot.refunds.get(rid)
        if refund is None:
            return _error("UNKNOWN_EVIDENCE_ID", f"refund {rid!r} not found")
        refund_total += int(refund.refund_amount_paise)

    expected_net = gross - fee - tax - refund_total
    return {
        "note": "exploratory — verifier is authoritative",
        "gross_paise": gross,
        "fee_paise": fee,
        "tax_paise": tax,
        "refund_total_paise": refund_total,
        "expected_net_paise": expected_net,
    }


def _check_date_window(
    dispatcher: ToolDispatcher,
    args: dict[str, Any],
) -> dict[str, Any]:
    record_ids = args.get("record_ids", [])
    if not isinstance(record_ids, list) or not record_ids:
        return _error("INVALID_ARGUMENTS", "record_ids must be a non-empty list")

    # Check if records fall within the posting/settlement window
    results: list[dict[str, Any]] = []
    for rid in record_ids:
        parsed = parse_evidence_id(rid)
        if parsed is None:
            results.append({"record_id": rid, "error": "bad format"})
            continue
        record_type, record_id = parsed
        record = dispatcher.snapshot.record(record_type, record_id)
        if record is None:
            results.append({"record_id": rid, "error": "not found"})
            continue

        info: dict[str, Any] = {"record_id": rid, "record_type": record_type}
        if isinstance(record, SettlementRecord):
            info["window_start"] = record.window_start_utc.isoformat()
            info["window_end"] = record.window_end_utc.isoformat()
            info["settled_at"] = record.settled_at_utc.isoformat()
        elif isinstance(record, LedgerEntryRecord):
            info["accounting_date"] = record.accounting_date.isoformat()
        elif isinstance(record, RefundRecord):
            info["created_at"] = record.created_at_utc.isoformat()
            posting_deadline = record.created_at_utc + timedelta(days=REFUND_POSTING_WINDOW_DAYS)
            info["posting_deadline"] = posting_deadline.isoformat()
        results.append(info)

    return {
        "note": "exploratory — verifier is authoritative",
        "bank_posting_window_s": BANK_POSTING_WINDOW_S,
        "refund_posting_window_days": REFUND_POSTING_WINDOW_DAYS,
        "records": results,
    }


def _check_unique_identity(
    dispatcher: ToolDispatcher,
    args: dict[str, Any],
) -> dict[str, Any]:
    record_ids = args.get("record_ids", [])
    if not isinstance(record_ids, list) or not record_ids:
        return _error("INVALID_ARGUMENTS", "record_ids must be a non-empty list")

    unique_ids: set[str] = set()
    duplicates: list[str] = []
    for rid in record_ids:
        if rid in unique_ids:
            duplicates.append(rid)
        else:
            unique_ids.add(rid)

    # Check twin groups for settlement ambiguity
    twin_settlement_ids = dispatcher.snapshot.twin_settlement_ids
    twin_hits = [rid for rid in record_ids if _extract_id(rid) in twin_settlement_ids]

    return {
        "note": "exploratory — verifier is authoritative",
        "unique_count": len(unique_ids),
        "duplicate_ids": duplicates,
        "twin_settlement_conflicts": twin_hits,
        "is_unique": len(duplicates) == 0 and len(twin_hits) == 0,
    }


def _extract_id(evidence_id: str) -> str:
    """Extract the record_id portion from ``TYPE:record_id``."""
    if ":" in evidence_id:
        return evidence_id.split(":", 1)[1]
    return evidence_id


_HANDLERS: dict[str, Callable[[ToolDispatcher, dict[str, Any]], dict[str, Any]]] = {
    "get_case": _get_case,
    "get_evidence_graph": _get_evidence_graph,
    "get_record": _get_record,
    "list_candidate_records": _list_candidate_records,
    "get_rule_manifest": _get_rule_manifest,
    "calculate_control_totals": _calculate_control_totals,
    "calculate_expected_net": _calculate_expected_net,
    "check_date_window": _check_date_window,
    "check_unique_identity": _check_unique_identity,
}
