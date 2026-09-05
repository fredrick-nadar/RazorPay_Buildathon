"""Deterministic, tool-specific case-evidence binding (REVIEW-013, REVIEW-015).

The evidence gate must prove that a model actually retrieved or validated a
FINANCIAL RECORD belonging to the case under investigation.

Two spoofs had to be closed:

REVIEW-013
    A generic scan of every ``*_id`` field let a model add the active
    ``case_id`` to ``calculate_control_totals``, which ignores its arguments
    and returns global run totals, and have the call count as case evidence.

REVIEW-015
    The replacement still kept the active ``case_id`` in the SAME identifier
    set as record ids, and compared with a type-erasing helper. So
    ``check_unique_identity(record_ids=[case_id])`` counted as evidence even
    though that handler resolves nothing against the snapshot, and
    ``REFUND:x`` counted as ``PAYMENT:x``.

The fix keeps two separate contracts:

* **Case identity** - only ``get_case`` (and the graph request argument) may be
  satisfied by the active ``case_id``.
* **Typed record identity** - record-oriented tools require an EXACT canonical
  ``TYPE:record_id`` that the case cites. There is no prefix stripping and no
  cross-type equivalence anywhere below, so a wrong-type record sharing a bare
  id is rejected, and the case id can never satisfy a record tool.

This is contract validation, not comprehension: it never judges whether the
model understood a result, only whether the call was bound to this case.

Tools deliberately excluded from the evidence gate:

``get_rule_manifest``
    Returns static rule metadata identical for every case.
``calculate_control_totals``
    Its handler ignores its arguments and returns global run totals, so a
    successful call proves nothing about this case.
``list_candidate_records``
    Its handler consumes only ``record_type``; ``case_id`` is neither required
    nor used, so the call carries no case binding to validate.

Making either genuinely case-bound would change tool behaviour and is left as
separately approved work.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.reconciliation.detectors import CaseRecord


@dataclass(frozen=True)
class CaseEvidenceIndex:
    """Case identity and typed record identity, deliberately kept apart.

    ``case_id`` is NOT a member of any record collection, so it cannot satisfy
    a record-oriented tool. ``typed_records`` holds exact canonical
    ``TYPE:record_id`` strings; ``payment_ids`` and ``refund_ids`` hold the bare
    ids of the correspondingly typed cited evidence, because
    ``calculate_expected_net`` is specified with bare ids per PRD 10.2.
    """

    case_id: str
    typed_records: frozenset[str]
    payment_ids: frozenset[str]
    refund_ids: frozenset[str]

    @property
    def graph_identifiers(self) -> frozenset[str]:
        """Exact graph node ids that would prove this case appears in a graph.

        ``app.graph.evidence`` emits ``CASE:<case_id>`` for case nodes and
        ``TYPE:record_id`` for record nodes, so those exact strings are the
        binding facts.
        """
        return frozenset({f"CASE:{self.case_id}"}) | self.typed_records


def build_case_evidence_index(case: CaseRecord) -> CaseEvidenceIndex:
    """Index one case for binding checks, keeping identity kinds separate."""
    typed: set[str] = set()
    payments: set[str] = set()
    refunds: set[str] = set()
    for item in case.evidence:
        typed.add(f"{item.record_type}:{item.record_id}")
        if item.record_type == "PAYMENT":
            payments.add(item.record_id)
        elif item.record_type == "REFUND":
            refunds.add(item.record_id)
    return CaseEvidenceIndex(
        case_id=case.case_id,
        typed_records=frozenset(typed),
        payment_ids=frozenset(payments),
        refund_ids=frozenset(refunds),
    )


def _exact_strings(value: Any) -> list[str]:
    """Non-empty strings from a scalar or sequence argument, as given."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str) and item]
    return []


# ---------------------------------------------------------------------------
# One validator per tool. Each reads ONLY the fields its handler consumes, and
# compares identifiers with EXACT equality against the correct identity kind.
# ---------------------------------------------------------------------------


def _bind_get_case(
    case: CaseRecord, args: dict[str, Any], observation: dict[str, Any], index: CaseEvidenceIndex
) -> bool:
    """``_get_case`` consumes ``case_id`` and echoes the resolved case id.

    This is the ONLY tool the active case id may satisfy.
    """
    return args.get("case_id") == index.case_id and observation.get("case_id") == index.case_id


def _bind_get_evidence_graph(
    case: CaseRecord, args: dict[str, Any], observation: dict[str, Any], index: CaseEvidenceIndex
) -> bool:
    """``_get_evidence_graph`` requires this case AND a graph that names it.

    Its handler ignores ``case_id`` and returns the COMPLETE run graph, so the
    handler itself is not case-scoped and this validator does not pretend it
    is. Relevance is established from the returned content instead: an exact
    ``CASE:<case_id>`` node or an exact typed cited record must be present.
    """
    if args.get("case_id") != index.case_id:
        return False
    return _graph_contains(observation, index.graph_identifiers)


def _graph_contains(payload: Any, wanted: frozenset[str], depth: int = 0) -> bool:
    """Exact-match search for a graph identifier. No prefix stripping.

    Bounded recursion so a malformed or hostile payload cannot cause unbounded
    work, and only string values are compared.
    """
    if depth > 6:
        return False
    if isinstance(payload, str):
        return payload in wanted
    if isinstance(payload, dict):
        return any(_graph_contains(value, wanted, depth + 1) for value in payload.values())
    if isinstance(payload, (list, tuple)):
        return any(_graph_contains(item, wanted, depth + 1) for item in payload)
    return False


def _bind_get_record(
    case: CaseRecord, args: dict[str, Any], observation: dict[str, Any], index: CaseEvidenceIndex
) -> bool:
    """``_get_record`` consumes ``record_id`` and returns that exact record.

    Requires an exact canonical ``TYPE:record_id`` this case cites. The handler
    resolves the id against the snapshot and errors on an unknown id, and
    ``is_case_evidence_call`` rejects errored dispatches, so a clean return
    means that specific cited record was retrieved.
    """
    return args.get("record_id") in index.typed_records


def _bind_get_records(
    case: CaseRecord, args: dict[str, Any], observation: dict[str, Any], index: CaseEvidenceIndex
) -> bool:
    """``_get_records`` must return at least one exact cited typed record."""
    consumed = _exact_strings(args.get("record_ids"))
    records = observation.get("records")
    if not isinstance(records, list):
        return False
    returned = {
        item.get("evidence_id")
        for item in records
        if isinstance(item, dict) and isinstance(item.get("record"), dict)
    }
    return any(
        identifier in index.typed_records and identifier in returned for identifier in consumed
    )


def _bind_check_date_window(
    case: CaseRecord, args: dict[str, Any], observation: dict[str, Any], index: CaseEvidenceIndex
) -> bool:
    """``_check_date_window`` consumes ``record_ids`` and resolves each one.

    Requires that at least one consumed id is an exact cited typed record AND
    that the handler actually resolved it, rather than reporting a per-record
    parse or lookup error.
    """
    consumed = _exact_strings(args.get("record_ids"))
    if not any(identifier in index.typed_records for identifier in consumed):
        return False
    records = observation.get("records")
    if not isinstance(records, list):
        return False
    return any(
        isinstance(entry, dict)
        and not entry.get("error")
        and entry.get("record_id") in index.typed_records
        for entry in records
    )


def _bind_check_unique_identity(
    case: CaseRecord, args: dict[str, Any], observation: dict[str, Any], index: CaseEvidenceIndex
) -> bool:
    """``_check_unique_identity`` consumes ``record_ids`` as identity tokens.

    This handler resolves NOTHING against the snapshot: it treats any string as
    a token. Exact typed membership is therefore the entire gate. That is what
    stops ``record_ids=[case_id]`` and ``REFUND:x`` for a cited ``PAYMENT:x``
    from counting (REVIEW-015).
    """
    consumed = _exact_strings(args.get("record_ids"))
    return any(identifier in index.typed_records for identifier in consumed)


def _bind_calculate_expected_net(
    case: CaseRecord, args: dict[str, Any], observation: dict[str, Any], index: CaseEvidenceIndex
) -> bool:
    """``_calculate_expected_net`` consumes bare ``payment_ids``/``refund_ids``.

    Each field is validated against its OWN type: a payment id supplied under
    ``refund_ids`` does not qualify, nor does the active case id, nor an
    unrelated id. Empty calculations cannot qualify.
    """
    payments = _exact_strings(args.get("payment_ids"))
    refunds = _exact_strings(args.get("refund_ids"))
    if any(identifier in index.payment_ids for identifier in payments):
        return True
    return any(identifier in index.refund_ids for identifier in refunds)


# Tools absent from this table can never satisfy the evidence gate.
_BINDERS: dict[
    str, Callable[[CaseRecord, dict[str, Any], dict[str, Any], CaseEvidenceIndex], bool]
] = {
    "get_case": _bind_get_case,
    "get_evidence_graph": _bind_get_evidence_graph,
    "get_record": _bind_get_record,
    "get_records": _bind_get_records,
    "check_date_window": _bind_check_date_window,
    "check_unique_identity": _bind_check_unique_identity,
    "calculate_expected_net": _bind_calculate_expected_net,
}

#: Tools that CAN satisfy the evidence gate, given a valid case binding.
EVIDENCE_BEARING_TOOLS: frozenset[str] = frozenset(_BINDERS)

#: The only tool the active case id may satisfy on its own.
CASE_IDENTITY_TOOLS: frozenset[str] = frozenset({"get_case"})


def is_case_evidence_call(
    case: CaseRecord,
    tool_name: str,
    arguments: dict[str, Any],
    observation: dict[str, Any],
    index: CaseEvidenceIndex | None = None,
) -> bool:
    """Did this dispatch retrieve or validate evidence bound to ``case``?

    Requires an evidence-bearing tool, a dispatch without error, and the
    tool-specific binding rule to hold. Forbidden tools, tool errors, static
    metadata, global calculations, the bare case id on a record tool,
    wrong-type records and unrelated identifiers all return False.
    """
    binder = _BINDERS.get(tool_name)
    if binder is None:
        return False
    if observation.get("error"):
        return False
    resolved = index if index is not None else build_case_evidence_index(case)
    return binder(case, arguments, observation, resolved)


__all__ = [
    "CASE_IDENTITY_TOOLS",
    "EVIDENCE_BEARING_TOOLS",
    "CaseEvidenceIndex",
    "build_case_evidence_index",
    "is_case_evidence_call",
]
