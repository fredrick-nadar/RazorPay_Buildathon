"""Evaluator-only benchmark comparison with frozen denominators (PRD 14).

Contract: this module is called only AFTER the runtime reconciliation output
is complete and finalized; it loads ground-truth labels itself, and runtime
code can never import it (label firewall). Every metric is reported with its
explicit numerator and denominator - never a percentage alone.

Frozen definitions:

- record-level match rate = correctly dispositioned matched eligible records
  / eligible canonical records. Duplicate deliveries and quarantined rows
  are reported separately and excluded from the denominator.
- match precision = correct deterministic relationships / all predicted
  deterministic relationships. Each predicted relationship is verified
  independently against the eligible view using the evaluator-side candidate
  rules, never the engine's logic.
- case classification accuracy = one-to-one matched cases / labelled cases.
  A runtime case qualifies only with the same category AND exactly the label
  anchor evidence set - extra unrelated evidence does not count as correct.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.evaluation import control_totals as ct
from app.evaluation.dataset_spec import INPUT_FILES

_PREFIX_TO_TYPE = {
    "pay_": "PAYMENT",
    "rfd_": "REFUND",
    "stl_": "SETTLEMENT",
    "bnk_": "BANK_ENTRY",
    "led_": "LEDGER_ENTRY",
}

RULE_LEDGER_SOURCE = "R-EXACT-LEDGER-SOURCE"
RULE_COMPOSITION = "R-UNIQUE-REFUND-COMPOSITION"


def _record_type_of(record_id: str) -> str:
    for prefix, record_type in sorted(_PREFIX_TO_TYPE.items()):
        if record_id.startswith(prefix):
            return record_type
    return "UNKNOWN"


def _eligible_index(ds: ct.DatasetRows) -> dict[str, dict[str, dict[str, str]]]:
    view = ct.eligible_view(ds)
    index: dict[str, dict[str, dict[str, str]]] = {
        "payments": {},
        "refunds": {},
        "settlements": {},
        "bank_entries": {},
        "ledger_entries": {},
    }
    for row in view.rows.payments:
        index["payments"][row["payment_id"]] = row
    for row in view.rows.refunds:
        index["refunds"][row["refund_id"]] = row
    for row in view.rows.settlements:
        index["settlements"][row["settlement_id"]] = row
    for row in view.rows.bank_entries:
        index["bank_entries"][row["bank_entry_id"]] = row
    for row in view.rows.ledger_entries:
        index["ledger_entries"][row["ledger_entry_id"]] = row
    return index


def _verify_relationship(
    match: dict[str, Any], ds: ct.DatasetRows, index: dict[str, dict[str, dict[str, str]]]
) -> tuple[bool, str]:
    members = match["members"]
    by_type: dict[str, list[dict[str, Any]]] = {}
    for member in members:
        by_type.setdefault(member["record_type"], []).append(member)
    relationship = match["relationship_type"]
    rule = match["rule_id"]

    def member_id(record_type: str) -> str | None:
        entries = by_type.get(record_type, [])
        return entries[0]["record_id"] if entries else None

    if relationship == "REFUND_OF_PAYMENT":
        refund_id, payment_id = member_id("REFUND"), member_id("PAYMENT")
        refund = index["refunds"].get(refund_id or "")
        if refund is None or payment_id is None:
            return False, "unknown record id"
        if refund["payment_id"] != payment_id:
            return False, "refund does not belong to payment"
        return True, ""

    if relationship == "MEMBER_OF_SETTLEMENT":
        settlement_id = member_id("SETTLEMENT")
        settlement = index["settlements"].get(settlement_id or "")
        if settlement is None:
            return False, "unknown settlement"
        total = 0
        for member in by_type.get("PAYMENT", []):
            payment = index["payments"].get(member["record_id"])
            if payment is None or payment["settlement_id"] != settlement_id:
                return False, f"payment {member['record_id']} not a member"
            if member["signed_contribution_paise"] != ct.payment_net(payment):
                return False, f"payment {member['record_id']} wrong contribution"
            total += member["signed_contribution_paise"]
        for member in by_type.get("REFUND", []):
            refund = index["refunds"].get(member["record_id"])
            if refund is None or refund["settlement_id"] != settlement_id:
                return False, f"refund {member['record_id']} not a member"
            expected = -ct.amount_of(refund, "refund_amount")
            if member["signed_contribution_paise"] != expected:
                return False, f"refund {member['record_id']} wrong contribution"
            total += member["signed_contribution_paise"]
        if total != ct.amount_of(settlement, "net_amount"):
            return False, "contributions do not sum to settlement net"
        return True, ""

    if relationship == "SETTLEMENT_BANK_CREDIT":
        settlement_id, credit_id = member_id("SETTLEMENT"), member_id("BANK_ENTRY")
        settlement = index["settlements"].get(settlement_id or "")
        credit = index["bank_entries"].get(credit_id or "")
        if settlement is None or credit is None:
            return False, "unknown record id"
        if settlement["utr"] and credit["utr"] and settlement["utr"] == credit["utr"]:
            if ct.amount_of(credit, "signed_amount") == ct.amount_of(settlement, "net_amount"):
                return True, ""
            return False, "UTR pair with incompatible amount"
        settlement_map = ct.settlement_credit_candidates(ds)
        credit_map = ct.credit_settlement_candidates(ds)
        if settlement_map.get(settlement_id or "") == [credit_id] and credit_map.get(
            credit_id or ""
        ) == [settlement_id]:
            return True, ""
        return False, "credit is not the unique amount-window candidate"

    if relationship == "LEDGER_SOURCE":
        ledger_id = member_id("LEDGER_ENTRY")
        ledger = index["ledger_entries"].get(ledger_id or "")
        if ledger is None:
            return False, "unknown ledger row"
        if rule == RULE_COMPOSITION:
            payment_id = member_id("PAYMENT")
            payment = index["payments"].get(payment_id or "")
            if payment is None or ledger["source_reference"] != payment_id:
                return False, "composition row does not reference the payment"
            if ct.amount_of(ledger, "signed_amount") >= 0:
                return False, "composition row is not a deduction"
            target = -ct.amount_of(ledger, "signed_amount")
            total = 0
            for member in by_type.get("REFUND", []):
                refund = index["refunds"].get(member["record_id"])
                if refund is None or refund["payment_id"] != payment_id:
                    return False, "component refund outside payment"
                total += ct.amount_of(refund, "refund_amount")
            if total != target:
                return False, "components do not sum to the deduction"
            return True, ""
        source_type = ledger["source_type"]
        reference = ledger["source_reference"]
        expected_member = by_type.get(source_type or "", [])
        if not expected_member or expected_member[0]["record_id"] != reference:
            return False, "ledger source does not resolve to member"
        if source_type == "PAYMENT":
            payment = index["payments"].get(reference or "")
            if payment is None:
                return False, "unknown payment"
            if ct.amount_of(ledger, "signed_amount") != ct.payment_net(payment):
                return False, "ledger amount differs from payment net"
        elif source_type == "REFUND":
            refund = index["refunds"].get(reference or "")
            if refund is None:
                return False, "unknown refund"
            if ct.amount_of(ledger, "signed_amount") != -ct.amount_of(refund, "refund_amount"):
                return False, "ledger amount differs from refund"
        elif source_type == "SETTLEMENT":
            settlement = index["settlements"].get(reference or "")
            if settlement is None:
                return False, "unknown settlement"
            if ct.amount_of(ledger, "signed_amount") != ct.amount_of(settlement, "net_amount"):
                return False, "ledger amount differs from settlement net"
        else:
            return False, f"unknown source type {source_type}"
        return True, ""

    return False, f"unknown relationship {relationship}"


def _match_cases_one_to_one(
    runtime_cases: list[dict[str, Any]], labelled_cases: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Greedy deterministic one-to-one matching on category + anchor evidence."""
    used: set[int] = set()
    pairs: list[dict[str, Any]] = []
    for label in labelled_cases:
        anchors = {
            f"{_record_type_of(str(item))}:{str(item)}"
            for item in label.get("expected_evidence_ids", [])
        }
        found = -1
        for position, runtime_case in enumerate(runtime_cases):
            if position in used:
                continue
            if runtime_case.get("category") != label.get("expected_category"):
                continue
            evidence = {
                f"{item['record_type']}:{item['record_id']}"
                for item in runtime_case.get("evidence", [])
            }
            if evidence == anchors:
                found = position
                break
        if found >= 0:
            used.add(found)
            pairs.append(
                {
                    "label_case_id": label.get("case_id"),
                    "runtime_case_id": runtime_cases[found].get("case_id"),
                    "category": label.get("expected_category"),
                }
            )
    matched_label_ids = {pair["label_case_id"] for pair in pairs}
    misses = [
        {
            "label_case_id": label.get("case_id"),
            "category": label.get("expected_category"),
        }
        for label in labelled_cases
        if label.get("case_id") not in matched_label_ids
    ]
    false_positives = [
        {
            "runtime_case_id": runtime_case.get("case_id"),
            "category": runtime_case.get("category"),
        }
        for position, runtime_case in enumerate(runtime_cases)
        if position not in used
    ]
    return pairs, false_positives, misses


def _verification_metrics(
    runtime_cases: list[dict[str, Any]],
    labelled_cases: list[dict[str, Any]],
    matched_pairs: list[dict[str, Any]],
    runtime_output: dict[str, Any],
) -> dict[str, Any]:
    """Evaluator-only Phase 3 verification metrics.

    Labels are loaded only in this module. Runtime output is judged on final
    status and code-derived delta, never given label fields during execution.
    """
    runtime_by_id = {str(case.get("case_id")): case for case in runtime_cases}
    labels_by_id = {str(label.get("case_id")): label for label in labelled_cases}
    label_by_runtime = {
        str(pair["runtime_case_id"]): labels_by_id[str(pair["label_case_id"])]
        for pair in matched_pairs
        if str(pair.get("label_case_id")) in labels_by_id
    }

    outcome_ok = 0
    delta_ok = 0
    false_passes: list[dict[str, Any]] = []
    escalation_expected = 0
    escalation_actual = 0
    money_weighted_error = 0
    for runtime_id, label in sorted(label_by_runtime.items()):
        runtime = runtime_by_id[runtime_id]
        expected_outcome = label.get("expected_outcome")
        expected_delta = label.get("expected_delta_paise")
        actual_status = runtime.get("status")
        actual_delta = runtime.get("proposed_delta_paise")
        if actual_status == expected_outcome:
            outcome_ok += 1
        if actual_delta == expected_delta:
            delta_ok += 1
        if actual_status in {"APPROVAL_REQUIRED", "VERIFIED_RESOLVED"} and (
            actual_status != expected_outcome or actual_delta != expected_delta
        ):
            false_passes.append(
                {
                    "runtime_case_id": runtime_id,
                    "expected_outcome": expected_outcome,
                    "actual_status": actual_status,
                    "expected_delta_paise": expected_delta,
                    "actual_delta_paise": actual_delta,
                }
            )
        if label.get("must_escalate") is True:
            escalation_expected += 1
            if actual_status == "UNRESOLVED":
                escalation_actual += 1
        if expected_delta is not None or actual_delta is not None:
            money_weighted_error += abs(int(actual_delta or 0) - int(expected_delta or 0))

    verification = runtime_output.get("verification", {})
    completeness = verification.get("passing_proof_completeness", {})
    proof_numerator = int(completeness.get("numerator") or 0)
    proof_denominator = int(completeness.get("denominator") or 0)
    dry_run_error = int(verification.get("dry_run_abs_variance_after_paise") or 0)
    denominator = len(labelled_cases)
    return {
        "outcome_agreement": {
            "numerator": outcome_ok,
            "denominator": denominator,
            "rate": round(outcome_ok / denominator, 6) if denominator else 0.0,
        },
        "delta_agreement": {
            "numerator": delta_ok,
            "denominator": denominator,
            "rate": round(delta_ok / denominator, 6) if denominator else 0.0,
        },
        "ambiguous_escalation": {
            "numerator": escalation_actual,
            "denominator": escalation_expected,
            "rate": (
                round(escalation_actual / escalation_expected, 6) if escalation_expected else 0.0
            ),
        },
        "false_verifier_passes": false_passes,
        "false_verifier_pass_count": len(false_passes),
        "proof_completeness": {
            "numerator": proof_numerator,
            "denominator": proof_denominator,
            "complete": proof_numerator == proof_denominator,
        },
        "money_weighted_dry_run_error_paise": money_weighted_error + dry_run_error,
        "runtime_verification_summary": verification,
    }


def evaluate_dataset(dataset_root: Path, runtime_output: dict[str, Any]) -> dict[str, Any]:
    """Compare a finalized runtime output against ground truth (evaluator-only)."""
    dataset_root = Path(dataset_root)
    ds = ct.parse_dataset(dataset_root)
    labels = ds.labels or {}
    labels_manifest = json.loads(
        (dataset_root / "labels" / "manifest.json").read_text(encoding="utf-8")
    )
    index = _eligible_index(ds)
    view = ct.eligible_view(ds)
    computed_eligible = sum(len(getattr(view.rows, name)) for name in INPUT_FILES)
    eligible = int(labels_manifest["eligible_row_count"])

    # Relationship precision (independent verification per relationship).
    matches = runtime_output.get("matches", [])
    correct_relationships = 0
    false_relationships: list[dict[str, Any]] = []
    false_record_keys: set[str] = set()
    for match in matches:
        ok, why = _verify_relationship(match, ds, index)
        if ok:
            correct_relationships += 1
            continue
        false_relationships.append({"match_id": match.get("match_id"), "reason": why})
        for member in match.get("members", []):
            false_record_keys.add(f"{member['record_type']}:{member['record_id']}")
    predicted = len(matches)

    # Record-level match rate over eligible canonical records.
    matched_keys = {
        f"{member['record_type']}:{member['record_id']}"
        for match in matches
        for member in match.get("members", [])
    }
    correctly_dispositioned = len(matched_keys - false_record_keys)

    # Case one-to-one comparison on category + anchor evidence.
    runtime_cases = runtime_output.get("cases", [])
    labelled_cases = list(labels.get("cases", []))
    pairs, false_positives, misses = _match_cases_one_to_one(runtime_cases, labelled_cases)
    verification_metrics = _verification_metrics(
        runtime_cases, labelled_cases, pairs, runtime_output
    )

    totals = runtime_output.get("financial_control_totals", {})
    labels_totals = labels_manifest.get("totals_paise", {})
    totals_key_map = {
        "payment_gross": "payment_gross_paise",
        "payment_fee": "payment_fee_paise",
        "payment_tax": "payment_tax_paise",
        "payment_net": "payment_net_paise",
        "refund_total": "refund_total_paise",
        "settlement_net": "settlement_net_paise",
        "bank_credit": "bank_credit_paise",
        "ledger_total": "ledger_total_paise",
        "ledger_by_account": "ledger_by_account_paise",
    }
    totals_equal = all(
        totals.get(runtime_key) == labels_totals.get(labels_key)
        for labels_key, runtime_key in totals_key_map.items()
    )

    clean_total = int(labels.get("clean_reference", {}).get("ledger_total_paise", 0))
    observed_total = int(totals.get("ledger_total_paise", 0))
    runtime_variance = int(totals.get("residual_abs_variance_paise", 0))
    evaluator_variance = abs(observed_total - clean_total)

    known_keys: set[str] = set()
    for name, records in index.items():
        column = {
            "payments": "payment_id",
            "refunds": "refund_id",
            "settlements": "settlement_id",
            "bank_entries": "bank_entry_id",
            "ledger_entries": "ledger_entry_id",
        }[name]
        known_keys.update(
            f"{_record_type_of(row[column])}:{row[column]}" for row in records.values()
        )
    known_keys.update(f"CASE:{case.get('case_id')}" for case in runtime_cases)
    graph_referentially_valid = all(
        f"{member['record_type']}:{member['record_id']}" in known_keys
        for match in matches
        for member in match.get("members", [])
    ) and all(
        f"{item['record_type']}:{item['record_id']}" in known_keys
        for case in runtime_cases
        for item in case.get("evidence", [])
    )

    timing = runtime_output.get("timing_metrics", {})
    quarantine_expected = int(labels_manifest["quarantine_expected_count"])
    duplicate_expected = int(labels_manifest["duplicate_delivery_count"])

    ledger_scope_runtime = sum(
        abs(int(case.get("variance_paise", 0)))
        for case in runtime_cases
        if case.get("variance_scope") == "LEDGER"
    )
    bank_scope_runtime = sum(
        abs(int(case.get("variance_paise", 0)))
        for case in runtime_cases
        if case.get("variance_scope") == "BANK"
    )

    def ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 6) if denominator else 0.0

    return {
        "dataset": dataset_root.name,
        "profile": labels.get("profile"),
        "labels_loaded_after_runtime_output": True,
        "counts": {
            "raw_row_count": runtime_output.get("raw_row_count"),
            "eligible_canonical_records": eligible,
            "computed_eligible_records": computed_eligible,
            "eligible_counts_match": computed_eligible == eligible,
            "runtime_accepted_records": runtime_output.get("eligible_record_count"),
            "runtime_accepted_matches_eligible": runtime_output.get("eligible_record_count")
            == eligible,
            "quarantined": {
                "runtime": runtime_output.get("quarantined_row_count"),
                "expected": quarantine_expected,
                "match": runtime_output.get("quarantined_row_count") == quarantine_expected,
            },
            "duplicate_deliveries": {
                "runtime": runtime_output.get("duplicate_delivery_count"),
                "expected": duplicate_expected,
                "match": runtime_output.get("duplicate_delivery_count") == duplicate_expected,
            },
            "row_accounting_identity_holds": runtime_output.get("row_accounting", {}).get(
                "identity_holds"
            ),
        },
        "metrics": {
            "record_match_rate": {
                "numerator": correctly_dispositioned,
                "denominator": eligible,
                "rate": ratio(correctly_dispositioned, eligible),
            },
            "match_precision": {
                "numerator": correct_relationships,
                "denominator": predicted,
                "rate": ratio(correct_relationships, predicted),
                "false_relationship_count": len(false_relationships),
            },
            "case_classification_accuracy": {
                "numerator": len(pairs),
                "denominator": len(labelled_cases),
                "rate": ratio(len(pairs), len(labelled_cases)),
            },
        },
        "case_comparison": {
            "matched_pairs": pairs,
            "false_positive_cases": false_positives,
            "missed_labels": misses,
        },
        "verification": verification_metrics,
        "false_relationships": false_relationships,
        "totals_comparison": {
            "runtime_totals": totals,
            "labels_manifest_totals": labels_totals,
            "equal": totals_equal,
        },
        "residual_variance": {
            "runtime_abs_sum_paise": runtime_variance,
            "runtime_ledger_scope_paise": ledger_scope_runtime,
            "runtime_bank_scope_paise": bank_scope_runtime,
            "evaluator_ledger_scope_paise": evaluator_variance,
            "ledger_scope_equal": ledger_scope_runtime == evaluator_variance,
            "note": (
                "evaluator derives the ledger-side residual independently from "
                "clean_reference; bank-side residual (missing bank evidence) is "
                "runtime-computed and cross-checked via case anchors"
            ),
            "equal": ledger_scope_runtime == evaluator_variance,
        },
        "throughput": {
            "records_per_second": timing.get("records_per_second"),
            "reconcile_seconds": timing.get("reconcile_seconds"),
            "eligible_records": eligible,
        },
        "graph": {
            "counts": (runtime_output.get("graph", {}) or {}).get("counts", {}),
            "referentially_valid": graph_referentially_valid,
        },
        "economic_output_hash": runtime_output.get("economic_output_hash"),
        "unaccounted_record_keys": runtime_output.get("unaccounted_record_keys", []),
    }
