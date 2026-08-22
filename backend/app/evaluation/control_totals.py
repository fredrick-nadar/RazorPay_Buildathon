"""Independent dataset checks: parse-back totals, conservation, integrity.

Every function here re-derives its facts from the written dataset files (CSV
rows plus the evaluator-only labels) - never from generator-internal state.
This is the independent assertion required by the PRD Phase 1 stop
conditions: the generator constructs anomalies, this module judges them.

Clean conservation vs post-injection variance (review correction):

- Pre-injection (clean) identities are checked against the corpus of eligible
  rows and against ``labels.clean_reference``.
- Post-injection the anomalous ledger is NOT required to equal the clean
  ledger. Instead, per case and by source reference:

      observed_ledger_sum(reference) + expected_delta_paise
          == expected_clean_sum(reference)

  where the expected clean sum is derived from the input CSVs alone
  (payment net, or minus the refund amount). The same holds in aggregate:

      observed_ledger_total + sum(non-null expected_delta_paise)
          == clean_reference.ledger_total_paise
          == sum(payment.net) - sum(refunds) + sum(settlement.net)

  Because each case is checked on its own reference, a duplicate posting and
  a missing posting can never cancel each other.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.money import paise_from_decimal_rupees
from app.evaluation.dataset_spec import COLUMNS, ID_COLUMNS, INPUT_FILES, parse_ts

# Documented evaluator-side candidate rule: a bank credit is a candidate for a
# settlement when the net amount is equal and the credit was posted within
# [window_start - 24h, window_end + 24h]. This is a fixture-construction
# assertion, not a matching engine.
CANDIDATE_WINDOW_TOLERANCE_S = 86_400

SOURCE_TYPE_FILES = {
    "PAYMENT": "payments",
    "REFUND": "refunds",
    "SETTLEMENT": "settlements",
    "BANK_ENTRY": "bank_entries",
}

Row = dict[str, str]


@dataclass(frozen=True)
class DatasetRows:
    payments: tuple[Row, ...]
    refunds: tuple[Row, ...]
    settlements: tuple[Row, ...]
    bank_entries: tuple[Row, ...]
    ledger_entries: tuple[Row, ...]
    labels: dict[str, Any] | None = None


@dataclass(frozen=True)
class EligibleView:
    rows: DatasetRows
    duplicate_rows: tuple[tuple[str, int, Row], ...]
    quarantine_rows: tuple[tuple[str, int, Row], ...]


def rows_to_dataset_rows(rows: dict[str, list[Row]], labels: dict[str, Any] | None) -> DatasetRows:
    return DatasetRows(
        payments=tuple(rows["payments"]),
        refunds=tuple(rows["refunds"]),
        settlements=tuple(rows["settlements"]),
        bank_entries=tuple(rows["bank_entries"]),
        ledger_entries=tuple(rows["ledger_entries"]),
        labels=labels,
    )


def _read_csv(path: Path) -> list[Row]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def parse_dataset(root: Path) -> DatasetRows:
    """Read a written dataset directory (inputs + evaluator-only labels)."""
    inputs = root / "inputs"
    labels_path = root / "labels" / "labels.json"
    labels: dict[str, Any] | None = None
    if labels_path.is_file():
        loaded: dict[str, Any] = json.loads(labels_path.read_text(encoding="utf-8"))
        labels = loaded
    rows = {name: _read_csv(inputs / f"{name}.csv") for name in INPUT_FILES}
    return rows_to_dataset_rows(rows, labels)


# ---------------------------------------------------------------------------
# Eligibility: quarantine-labelled rows out, exact duplicate deliveries deduped.
# ---------------------------------------------------------------------------


def _row_expectations(labels: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not labels:
        return []
    raw = labels.get("row_expectations", [])
    return [dict(entry) for entry in raw]


def _expectation_keys(labels: dict[str, Any] | None, prefix: str) -> set[tuple[str, int]]:
    keys: set[tuple[str, int]] = set()
    for entry in _row_expectations(labels):
        expectation = str(entry.get("expectation", ""))
        if expectation == prefix or expectation.startswith(prefix):
            file_name = str(entry.get("file", ""))
            stem = file_name.removesuffix(".csv")
            row_number = entry.get("row_number")
            if row_number is not None:
                keys.add((stem, int(row_number)))
    return keys


def quarantine_keys(labels: dict[str, Any] | None) -> set[tuple[str, int]]:
    return _expectation_keys(labels, "QUARANTINE")


def duplicate_delivery_keys(labels: dict[str, Any] | None) -> set[tuple[str, int]]:
    keys: set[tuple[str, int]] = set()
    for entry in _row_expectations(labels):
        if str(entry.get("expectation", "")) == "DUPLICATE_DELIVERY":
            stem = str(entry.get("file", "")).removesuffix(".csv")
            row_number = entry.get("row_number")
            if row_number is not None:
                keys.add((stem, int(row_number)))
    return keys


def eligible_view(ds: DatasetRows) -> EligibleView:
    """Remove quarantine-labelled rows and exact duplicate deliveries.

    A repeated identifier is only tolerated when the full row content is
    identical AND the row is labelled DUPLICATE_DELIVERY; conflicting rows
    are kept so the referential-integrity check can flag them.
    """
    qkeys = quarantine_keys(ds.labels)
    dkeys = duplicate_delivery_keys(ds.labels)
    kept: dict[str, list[Row]] = {}
    duplicates: list[tuple[str, int, Row]] = []
    quarantined: list[tuple[str, int, Row]] = []
    for name in INPUT_FILES:
        raw: tuple[Row, ...] = getattr(ds, name)
        file_rows: list[Row] = []
        seen: dict[str, Row] = {}
        for index, row in enumerate(raw, start=1):
            if (name, index) in qkeys:
                quarantined.append((name, index, row))
                continue
            key = row[ID_COLUMNS[name]]
            prior = seen.get(key)
            if prior is not None and prior == row:
                if (name, index) not in dkeys:
                    # Keep it visible for the referential check to flag.
                    file_rows.append(row)
                else:
                    duplicates.append((name, index, row))
                continue
            seen.setdefault(key, row)
            file_rows.append(row)
        kept[name] = file_rows
    view = rows_to_dataset_rows(kept, ds.labels)
    return EligibleView(
        rows=view,
        duplicate_rows=tuple(duplicates),
        quarantine_rows=tuple(quarantined),
    )


def amount_of(row: Row, column: str) -> int:
    return int(paise_from_decimal_rupees(row[column]))


def payment_net(row: Row) -> int:
    return (
        amount_of(row, "gross_amount") - amount_of(row, "fee_amount") - amount_of(row, "tax_amount")
    )


# ---------------------------------------------------------------------------
# Totals (over eligible, deduplicated rows).
# ---------------------------------------------------------------------------


def compute_totals(ds: DatasetRows) -> dict[str, Any]:
    payments_gross = sum(amount_of(r, "gross_amount") for r in ds.payments)
    payments_fee = sum(amount_of(r, "fee_amount") for r in ds.payments)
    payments_tax = sum(amount_of(r, "tax_amount") for r in ds.payments)
    refunds_total = sum(amount_of(r, "refund_amount") for r in ds.refunds)
    settlements_net = sum(amount_of(r, "net_amount") for r in ds.settlements)
    bank_total = sum(amount_of(r, "signed_amount") for r in ds.bank_entries)
    by_account: dict[str, int] = {}
    for row in ds.ledger_entries:
        by_account[row["account_code"]] = by_account.get(row["account_code"], 0) + amount_of(
            row, "signed_amount"
        )
    return {
        "payment_gross": payments_gross,
        "payment_fee": payments_fee,
        "payment_tax": payments_tax,
        "payment_net": payments_gross - payments_fee - payments_tax,
        "refund_total": refunds_total,
        "settlement_net": settlements_net,
        "bank_credit": bank_total,
        "ledger_total": sum(by_account.values()),
        "ledger_by_account": dict(sorted(by_account.items())),
    }


def eligible_metrics(ds: DatasetRows) -> dict[str, Any]:
    """Anomaly-aware metrics; evaluator-only (labels/manifest.json material)."""
    view = eligible_view(ds)
    eligible_count = sum(len(getattr(view.rows, name)) for name in INPUT_FILES)
    return {
        "eligible_row_count": eligible_count,
        "quarantine_expected_count": len(view.quarantine_rows),
        "duplicate_delivery_count": len(view.duplicate_rows),
        "totals_paise": compute_totals(view.rows),
    }


# ---------------------------------------------------------------------------
# Conservation identities.
# ---------------------------------------------------------------------------


def _require_labels(ds: DatasetRows) -> dict[str, Any] | None:
    return ds.labels


def settlement_conservation_violations(ds: DatasetRows) -> list[str]:
    """Each settlement must conserve net = gross - fee - tax + adjustment."""
    problems: list[str] = []
    view = eligible_view(ds)
    for row in view.rows.settlements:
        net = amount_of(row, "net_amount")
        expected = (
            amount_of(row, "gross_credit")
            - amount_of(row, "fee_amount")
            - amount_of(row, "tax_amount")
            + amount_of(row, "adjustment_amount")
        )
        if net != expected:
            problems.append(
                f"settlement {row['settlement_id']} does not conserve value: "
                f"net {net} != gross-fee-tax+adjustment {expected}"
            )
    return problems


def _settlements_without_credit(view_rows: DatasetRows) -> list[str]:
    credits = [
        (amount_of(credit, "signed_amount"), parse_ts(credit["posted_at_utc"]))
        for credit in view_rows.bank_entries
    ]
    missing: list[str] = []
    for settlement in view_rows.settlements:
        net = amount_of(settlement, "net_amount")
        start = parse_ts(settlement["window_start_utc"])
        end = parse_ts(settlement["window_end_utc"])
        has_credit = any(
            amount == net
            and start - CANDIDATE_WINDOW_TOLERANCE_S <= posted <= end + CANDIDATE_WINDOW_TOLERANCE_S
            for amount, posted in credits
        )
        if not has_credit:
            missing.append(settlement["settlement_id"])
    return missing


def _ambiguous_case_evidence(ds: DatasetRows) -> list[list[str]]:
    labels = _require_labels(ds)
    if not labels:
        return []
    evidence: list[list[str]] = []
    for case in labels.get("cases", []):
        if str(case.get("expected_category", "")) == "AMBIGUOUS_EVIDENCE":
            evidence.append([str(item) for item in case.get("expected_evidence_ids", [])])
    return evidence


def corpus_identity_violations(ds: DatasetRows) -> list[str]:
    """Corpus-level clean identities over eligible rows.

    ``sum(settlement.net) == sum(payment.net) - sum(refunds)`` must hold
    exactly; the bank credit total must equal the settlement total minus the
    settlements whose missing bank evidence is an explicitly labelled case;
    ledger account totals must equal their source-side sums.
    """
    problems: list[str] = []
    view = eligible_view(ds)
    totals = compute_totals(view.rows)
    if totals["settlement_net"] != totals["payment_net"] - totals["refund_total"]:
        problems.append(
            "corpus identity broken: settlement net total "
            f"{totals['settlement_net']} != payment net {totals['payment_net']} "
            f"- refunds {totals['refund_total']}"
        )
    labelled = {sid for evidence in _ambiguous_case_evidence(ds) for sid in evidence}
    missing_bank = _settlements_without_credit(view.rows)
    for sid in missing_bank:
        if sid not in labelled:
            problems.append(f"settlement {sid} has no bank credit but is not case-labelled")
    missing_net = sum(
        amount_of(row, "net_amount")
        for row in view.rows.settlements
        if row["settlement_id"] in set(missing_bank)
    )
    if totals["bank_credit"] != totals["settlement_net"] - missing_net:
        problems.append(
            "bank identity broken: bank credit total "
            f"{totals['bank_credit']} != settlement net total "
            f"{totals['settlement_net']} minus missing-evidence nets {missing_net}"
        )
    by_account = totals["ledger_by_account"]
    expected_1100 = totals["settlement_net"]
    expected_2100 = totals["payment_net"] - totals["refund_total"]
    labels = _require_labels(ds)
    delta_total = 0
    if labels:
        for case in labels.get("cases", []):
            delta = case.get("expected_delta_paise")
            if delta is not None:
                delta_total += int(delta)
    if by_account.get("1100-BANK-OPERATING", 0) != expected_1100:
        problems.append(
            f"ledger 1100 total {by_account.get('1100-BANK-OPERATING', 0)} != "
            f"settlement net total {expected_1100}"
        )
    observed_2100 = by_account.get("2100-PAYMENTS-CLEARING", 0)
    if observed_2100 + delta_total != expected_2100:
        problems.append(
            f"ledger 2100 total {observed_2100} + deltas {delta_total} != "
            f"payment net minus refunds {expected_2100}"
        )
    return problems


# ---------------------------------------------------------------------------
# Candidate rules (review correction: four separate assertions).
# ---------------------------------------------------------------------------


def credit_settlement_candidates(ds: DatasetRows) -> dict[str, list[str]]:
    """bank_entry_id -> settlement ids valid under the documented rule."""
    view = eligible_view(ds)
    settlements = [
        (
            row["settlement_id"],
            amount_of(row, "net_amount"),
            parse_ts(row["window_start_utc"]),
            parse_ts(row["window_end_utc"]),
        )
        for row in view.rows.settlements
    ]
    result: dict[str, list[str]] = {}
    for credit in view.rows.bank_entries:
        amount = amount_of(credit, "signed_amount")
        posted = parse_ts(credit["posted_at_utc"])
        matches = [
            sid
            for sid, net, start, end in settlements
            if net == amount
            and start - CANDIDATE_WINDOW_TOLERANCE_S <= posted <= end + CANDIDATE_WINDOW_TOLERANCE_S
        ]
        result[credit["bank_entry_id"]] = sorted(matches)
    return result


def settlement_credit_candidates(ds: DatasetRows) -> dict[str, list[str]]:
    """settlement_id -> bank credit ids valid under the documented rule."""
    view = eligible_view(ds)
    credits = [
        (row["bank_entry_id"], amount_of(row, "signed_amount"), parse_ts(row["posted_at_utc"]))
        for row in view.rows.bank_entries
    ]
    result: dict[str, list[str]] = {}
    for settlement in view.rows.settlements:
        net = amount_of(settlement, "net_amount")
        start = parse_ts(settlement["window_start_utc"])
        end = parse_ts(settlement["window_end_utc"])
        matches = [
            bid
            for bid, amount, posted in credits
            if net == amount
            and start - CANDIDATE_WINDOW_TOLERANCE_S <= posted <= end + CANDIDATE_WINDOW_TOLERANCE_S
        ]
        result[settlement["settlement_id"]] = sorted(matches)
    return result


def refund_composition_candidates(
    ds: DatasetRows, payment_id: str, amount_paise: int
) -> list[list[str]]:
    """Refund subsets of one payment whose sum equals ``amount_paise``.

    Documented rule for the partial-refund ambiguity: a ledger
    refund-deduction row attributed to a payment may reverse any subset of
    that payment's recorded refunds whose amounts sum to the row amount.
    """
    view = eligible_view(ds)
    refunds = sorted(
        (row["refund_id"], amount_of(row, "refund_amount"))
        for row in view.rows.refunds
        if row["payment_id"] == payment_id
    )
    if len(refunds) > 16:
        return []
    candidates: list[list[str]] = []
    for mask in range(1, 1 << len(refunds)):
        subset = [refunds[i] for i in range(len(refunds)) if mask & (1 << i)]
        if sum(amount for _, amount in subset) == amount_paise:
            candidates.append([rid for rid, _ in subset])
    return sorted(candidates)


def candidate_count_violations(ds: DatasetRows) -> list[str]:
    """The four separated candidate-count rules (review correction 2).

    - normal settlement and the UTR-less-but-unique settlement: exactly one
      candidate credit (and each such credit has exactly one candidate);
    - the missing-bank-evidence case: exactly zero candidate credits and it
      must be an explicitly labelled AMBIGUOUS case without bank evidence;
    - twin-settlement ambiguity: each twin credit has exactly two candidate
      settlements, and each twin settlement has exactly two candidate credits;
    - partial-refund ambiguity: at least two refund composition candidates
      for the aggregate ledger amount.
    """
    labels = _require_labels(ds)
    if labels is None:
        return ["labels missing: candidate rules cannot be verified"]
    problems: list[str] = []
    view = eligible_view(ds)
    credit_map = credit_settlement_candidates(ds)
    settlement_map = settlement_credit_candidates(ds)

    twin_settlements: set[str] = set()
    twin_credits: set[str] = set()
    missing_bank_settlements: set[str] = set()
    partial_refund_payments: set[str] = set()
    for case in labels.get("cases", []):
        if str(case.get("expected_category", "")) != "AMBIGUOUS_EVIDENCE":
            continue
        evidence = [str(item) for item in case.get("expected_evidence_ids", [])]
        settlement_ids = [e for e in evidence if e.startswith("stl_")]
        bank_ids = [e for e in evidence if e.startswith("bnk_")]
        payment_ids = [e for e in evidence if e.startswith("pay_")]
        refund_ids = [e for e in evidence if e.startswith("rfd_")]
        if len(settlement_ids) == 2 and len(bank_ids) == 2:
            twin_settlements.update(settlement_ids)
            twin_credits.update(bank_ids)
        elif len(settlement_ids) == 1 and not bank_ids:
            missing_bank_settlements.update(settlement_ids)
        elif payment_ids and len(refund_ids) >= 2:
            partial_refund_payments.update(payment_ids)

    for credit_id, candidates in sorted(credit_map.items()):
        if credit_id in twin_credits:
            if len(candidates) != 2:
                problems.append(
                    f"twin credit {credit_id} has {len(candidates)} settlement "
                    "candidates, expected exactly 2"
                )
        elif len(candidates) != 1:
            problems.append(
                f"credit {credit_id} has {len(candidates)} settlement candidates, "
                "expected exactly 1"
            )
    for settlement_id, candidates in sorted(settlement_map.items()):
        if settlement_id in twin_settlements:
            if len(candidates) != 2:
                problems.append(
                    f"twin settlement {settlement_id} has {len(candidates)} credit "
                    "candidates, expected exactly 2"
                )
        elif settlement_id in missing_bank_settlements:
            if len(candidates) != 0:
                problems.append(
                    f"missing-bank settlement {settlement_id} has {len(candidates)} "
                    "credit candidates, expected exactly 0"
                )
        elif len(candidates) != 1:
            problems.append(
                f"settlement {settlement_id} has {len(candidates)} credit candidates, "
                "expected exactly 1"
            )

    if partial_refund_payments:
        payments_by_id = {row["payment_id"]: row for row in view.rows.payments}
        for payment_id in sorted(partial_refund_payments):
            if payment_id not in payments_by_id:
                problems.append(f"partial-refund case cites unknown payment {payment_id}")
                continue
            aggregate_rows = [
                row
                for row in view.rows.ledger_entries
                if row["source_reference"] == payment_id
                and row["source_type"] == "PAYMENT"
                and amount_of(row, "signed_amount") < 0
            ]
            if not aggregate_rows:
                problems.append(
                    f"partial-refund payment {payment_id} has no aggregate deduction rows"
                )
                continue
            for row in aggregate_rows:
                amount = -amount_of(row, "signed_amount")
                compositions = refund_composition_candidates(ds, payment_id, amount)
                if len(compositions) < 2:
                    problems.append(
                        f"partial-refund aggregate row {row['ledger_entry_id']} has "
                        f"{len(compositions)} refund composition candidates, "
                        "expected at least 2"
                    )
    return problems


# ---------------------------------------------------------------------------
# Referential integrity (review correction 2).
# ---------------------------------------------------------------------------


def referential_integrity_violations(ds: DatasetRows) -> list[str]:
    """Cross-file reference checks plus identifier uniqueness.

    The only tolerated identifier repeat is an exact duplicate-delivery row
    explicitly labelled DUPLICATE_DELIVERY. Quarantine-labelled rows are
    excluded from strict reference checks (they are untrusted by definition)
    but must exist in the raw files.
    """
    problems: list[str] = []
    labels = _require_labels(ds)
    raw_ids: dict[str, set[str]] = {}
    for name in INPUT_FILES:
        raw: tuple[Row, ...] = getattr(ds, name)
        id_column = ID_COLUMNS[name]
        dkeys = duplicate_delivery_keys(labels)
        seen: dict[str, Row] = {}
        for index, row in enumerate(raw, start=1):
            key = row[id_column]
            prior = seen.get(key)
            if prior is None:
                seen[key] = row
            elif prior == row:
                if (name, index) not in dkeys:
                    problems.append(
                        f"{name}: exact duplicate row {index} for {key} is not "
                        "labelled DUPLICATE_DELIVERY"
                    )
            else:
                problems.append(
                    f"{name}: identifier {key} repeated with conflicting content at row {index}"
                )
        raw_ids[name] = set(seen)

    qkeys = quarantine_keys(labels)
    view = eligible_view(ds)
    quarantined_positions = {(name, index) for name, index, _ in view.quarantine_rows}
    if quarantined_positions - qkeys:
        problems.append("internal error: quarantined rows outside labelled keys")

    for row in view.rows.refunds:
        if row["payment_id"] not in raw_ids["payments"]:
            problems.append(f"refund {row['refund_id']} references unknown payment")
    for row in view.rows.payments:
        settlement_id = row["settlement_id"]
        if settlement_id and settlement_id not in raw_ids["settlements"]:
            problems.append(
                f"payment {row['payment_id']} references unknown settlement {settlement_id}"
            )
    for row in ds.ledger_entries:
        source_type = row["source_type"]
        file_name = SOURCE_TYPE_FILES.get(source_type)
        if file_name is None:
            problems.append(
                f"ledger {row['ledger_entry_id']} has unknown source_type {source_type!r}"
            )
            continue
        if row["source_reference"] not in raw_ids[file_name]:
            problems.append(
                f"ledger {row['ledger_entry_id']} reference {row['source_reference']} "
                f"does not resolve in {file_name}"
            )

    settlement_utrs: dict[str, list[str]] = {}
    for row in ds.settlements:
        utr = row["utr"]
        if utr:
            settlement_utrs.setdefault(utr, []).append(row["settlement_id"])
    bank_utrs: dict[str, list[str]] = {}
    for row in ds.bank_entries:
        utr = row["utr"]
        if utr:
            bank_utrs.setdefault(utr, []).append(row["bank_entry_id"])
    # A settlement whose missing bank credit is an explicitly labelled case is
    # the one tolerated exception to 1:1 UTR pairing (its UTR has no credit).
    labelled = {sid for evidence in _ambiguous_case_evidence(ds) for sid in evidence}
    missing_bank = set(_settlements_without_credit(eligible_view(ds).rows))
    for utr, sids in sorted(settlement_utrs.items()):
        if len(sids) > 1:
            problems.append(f"UTR {utr} appears on multiple settlements: {sorted(sids)}")
            continue
        pairs = len(bank_utrs.get(utr, []))
        if pairs == 1:
            continue
        if pairs == 0 and sids[0] in missing_bank and sids[0] in labelled:
            continue
        problems.append(f"settlement UTR {utr} pairs with {pairs} bank entries")
    for utr, bids in sorted(bank_utrs.items()):
        if len(bids) > 1:
            problems.append(f"UTR {utr} appears on multiple bank entries: {sorted(bids)}")
        if len(settlement_utrs.get(utr, [])) != 1:
            problems.append(
                f"bank UTR {utr} pairs with {len(settlement_utrs.get(utr, []))} settlements"
            )

    for entry in _row_expectations(labels):
        stem = str(entry.get("file", "")).removesuffix(".csv")
        row_number = entry.get("row_number")
        if row_number is None:
            continue
        if stem in INPUT_FILES:
            stem_rows: tuple[Row, ...] = getattr(ds, stem)
            if not 1 <= int(row_number) <= len(stem_rows):
                problems.append(
                    f"row expectation {entry.get('expectation')} references missing "
                    f"row {stem}:{row_number}"
                )
    return problems


# ---------------------------------------------------------------------------
# Variance equation (review correction 1).
# ---------------------------------------------------------------------------


def _ledger_sums(ds: DatasetRows) -> tuple[dict[str, int], dict[str, int], int]:
    by_reference: dict[str, int] = {}
    by_account: dict[str, int] = {}
    total = 0
    for row in eligible_view(ds).rows.ledger_entries:
        signed = amount_of(row, "signed_amount")
        reference = row["source_reference"]
        by_reference[reference] = by_reference.get(reference, 0) + signed
        by_account[row["account_code"]] = by_account.get(row["account_code"], 0) + signed
        total += signed
    return by_reference, by_account, total


def variance_equation_violations(ds: DatasetRows) -> list[str]:
    """Per-case, per-reference, and aggregate post-injection variance proof."""
    labels = _require_labels(ds)
    if labels is None:
        return ["labels missing: variance equation cannot be verified"]
    problems: list[str] = []
    view = eligible_view(ds)
    by_reference, by_account, observed_total = _ledger_sums(ds)
    payments_by_id = {row["payment_id"]: row for row in view.rows.payments}
    refunds_by_id = {row["refund_id"]: row for row in view.rows.refunds}
    settlements_by_id = {row["settlement_id"]: row for row in view.rows.settlements}

    delta_total = 0
    for case in labels.get("cases", []):
        delta = case.get("expected_delta_paise")
        if delta is None:
            continue
        delta_total += int(delta)
        category = str(case.get("expected_category", ""))
        evidence = [str(item) for item in case.get("expected_evidence_ids", [])]
        if category == "DUPLICATE_LEDGER_POSTING":
            refs = [e for e in evidence if e.startswith("pay_")]
            if len(refs) != 1:
                problems.append(f"case {case.get('case_id')}: expected one payment reference")
                continue
            expected = payment_net(payments_by_id[refs[0]])
        elif category == "MISSING_REFUND_POSTING":
            refs = [e for e in evidence if e.startswith("rfd_")]
            if len(refs) != 1:
                problems.append(f"case {case.get('case_id')}: expected one refund reference")
                continue
            expected = -amount_of(refunds_by_id[refs[0]], "refund_amount")
        elif category == "SETTLEMENT_TIMING_WINDOW_SHIFT":
            refs = [e for e in evidence if e.startswith("stl_")]
            if len(refs) != 1:
                problems.append(f"case {case.get('case_id')}: expected one settlement reference")
                continue
            expected = amount_of(settlements_by_id[refs[0]], "net_amount")
        else:
            continue
        observed = by_reference.get(refs[0], 0)
        if observed + int(delta) != expected:
            problems.append(
                f"case {case.get('case_id')} reference {refs[0]}: observed {observed} "
                f"+ delta {int(delta)} != expected clean {expected}"
            )

    clean_reference = labels.get("clean_reference", {})
    clean_total = int(clean_reference.get("ledger_total_paise", 0))
    if observed_total + delta_total != clean_total:
        problems.append(
            f"aggregate variance equation broken: observed {observed_total} + deltas "
            f"{delta_total} != clean reference {clean_total}"
        )
    totals = compute_totals(view.rows)
    derived_clean = totals["payment_net"] - totals["refund_total"] + totals["settlement_net"]
    if clean_total != derived_clean:
        problems.append(
            f"clean reference {clean_total} != input-derived clean total {derived_clean}"
        )
    for account, clean_amount in sorted(clean_reference.get("ledger_by_account_paise", {}).items()):
        observed = by_account.get(account, 0)
        if account == "2100-PAYMENTS-CLEARING":
            if observed + delta_total != int(clean_amount):
                problems.append(
                    f"account {account}: observed {observed} + deltas {delta_total} "
                    f"!= clean {int(clean_amount)}"
                )
        elif observed != int(clean_amount):
            problems.append(f"account {account}: observed {observed} != clean {int(clean_amount)}")
    return problems


# ---------------------------------------------------------------------------
# Clean-structure checks ("clean records remain clean").
# ---------------------------------------------------------------------------


def clean_structure_violations(ds: DatasetRows) -> list[str]:
    """Every uncited source event has exactly one correct ledger row.

    Cited references are checked against their case semantics instead:
    duplicates have two identical rows, missing-refund references have zero
    rows, and timing-shift references keep one row booked outside the window.
    """
    labels = _require_labels(ds)
    if labels is None:
        return ["labels missing: clean-structure checks cannot run"]
    problems: list[str] = []
    view = eligible_view(ds)
    ledger_by_reference: dict[str, list[Row]] = {}
    for row in view.rows.ledger_entries:
        ledger_by_reference.setdefault(row["source_reference"], []).append(row)

    cited: set[str] = set()
    case_by_reference: dict[str, dict[str, Any]] = {}
    for case in labels.get("cases", []):
        for item in case.get("expected_evidence_ids", []):
            key = str(item)
            cited.add(key)
            case_by_reference.setdefault(key, case)

    for row in view.rows.payments:
        pid = row["payment_id"]
        expected = payment_net(row)
        rows = [r for r in ledger_by_reference.get(pid, []) if r["source_type"] == "PAYMENT"]
        case = case_by_reference.get(pid)
        category = str(case.get("expected_category")) if case is not None else ""
        if not category and not (
            len(rows) == 1 and amount_of(rows[0], "signed_amount") == expected
        ):
            problems.append(
                f"clean payment {pid} has {len(rows)} ledger rows, expected exactly "
                "1 with the correct net"
            )
        elif category == "DUPLICATE_LEDGER_POSTING" and not (
            len(rows) == 2 and all(amount_of(r, "signed_amount") == expected for r in rows)
        ):
            problems.append(f"duplicate-case payment {pid} must have exactly 2 net-equal rows")

    for row in view.rows.refunds:
        rid = row["refund_id"]
        expected = -amount_of(row, "refund_amount")
        rows = [r for r in ledger_by_reference.get(rid, []) if r["source_type"] == "REFUND"]
        case = case_by_reference.get(rid)
        category = str(case.get("expected_category")) if case is not None else ""
        if not category and not (
            len(rows) == 1 and amount_of(rows[0], "signed_amount") == expected
        ):
            problems.append(f"clean refund {rid} has {len(rows)} ledger rows, expected exactly 1")
        elif category == "MISSING_REFUND_POSTING" and rows:
            problems.append(f"missing-refund {rid} must have zero ledger rows")

    for row in view.rows.settlements:
        sid = row["settlement_id"]
        expected = amount_of(row, "net_amount")
        window_start_date = row["window_start_utc"][:10]
        window_end_date = row["window_end_utc"][:10]
        rows = [r for r in ledger_by_reference.get(sid, []) if r["source_type"] == "SETTLEMENT"]
        case = case_by_reference.get(sid)
        if case is None:
            ok = len(rows) == 1 and amount_of(rows[0], "signed_amount") == expected
            if ok:
                booked = rows[0]["accounting_date"]
                ok = window_start_date <= booked <= window_end_date
            if not ok:
                problems.append(
                    f"clean settlement {sid} must have exactly 1 net-equal ledger row "
                    "booked inside its window"
                )
        elif str(case.get("expected_category")) == "SETTLEMENT_TIMING_WINDOW_SHIFT":
            ok = len(rows) == 1 and amount_of(rows[0], "signed_amount") == expected
            if ok:
                booked = rows[0]["accounting_date"]
                ok = booked < window_start_date or booked > window_end_date
            if not ok:
                problems.append(
                    f"timing-shift settlement {sid} must keep 1 net-equal row booked "
                    "outside its window"
                )
    return problems


# ---------------------------------------------------------------------------
# Manifest checks.
# ---------------------------------------------------------------------------

ROOT_MANIFEST_ALLOWED_KEYS = {
    "dataset_version",
    "profile",
    "seed",
    "files",
    "reproducibility_hash",
}

LABELS_MANIFEST_REQUIRED_KEYS = {
    "label_schema_version",
    "dataset_version",
    "profile",
    "seed",
    "labels_sha256",
    "case_count",
    "row_expectation_count",
    "eligible_row_count",
    "quarantine_expected_count",
    "duplicate_delivery_count",
    "totals_paise",
}

LABEL_FIELD_NAMES = (
    "expected_category",
    "expected_outcome",
    "expected_delta_paise",
    "expected_evidence_ids",
    "must_escalate",
    "authoring_notes",
    "labels_sha256",
    "label_schema_version",
    "clean_reference",
    "row_expectations",
)


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def root_manifest_violations(root: Path) -> list[str]:
    """The root manifest must be strictly input-only raw file facts."""
    problems: list[str] = []
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return [f"missing root manifest at {manifest_path}"]
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    extra = sorted(set(manifest) - ROOT_MANIFEST_ALLOWED_KEYS)
    if extra:
        problems.append(f"root manifest contains non-raw keys: {extra}")
    text = manifest_path.read_text(encoding="utf-8")
    for field_name in LABEL_FIELD_NAMES:
        if field_name in text:
            problems.append(f"root manifest mentions label field {field_name}")
    for relative, info in sorted(manifest.get("files", {}).items()):
        path = root / relative
        if not path.is_file():
            problems.append(f"manifest references missing file {relative}")
            continue
        actual = _sha256_file(path)
        if info.get("sha256") != actual:
            problems.append(f"manifest sha256 mismatch for {relative}")
        with path.open("r", encoding="utf-8", newline="") as handle:
            row_count = sum(1 for _ in handle) - 1
        if info.get("rows") != row_count:
            problems.append(
                f"manifest row count mismatch for {relative}: {info.get('rows')} != {row_count}"
            )
        name = relative.removesuffix(".csv").removeprefix("inputs/")
        if tuple(info.get("columns", ())) != COLUMNS.get(name, ()):
            problems.append(f"manifest columns mismatch for {relative}")
    repro = manifest.get("reproducibility_hash", "")
    if len(repro) != 64 or any(c not in "0123456789abcdef" for c in repro):
        problems.append("root manifest reproducibility_hash is not a sha256 hex digest")
    return problems


def labels_manifest_violations(root: Path) -> list[str]:
    """labels/manifest.json must hash labels.json and carry anomaly-aware metrics."""
    problems: list[str] = []
    labels_path = root / "labels" / "labels.json"
    manifest_path = root / "labels" / "manifest.json"
    if not labels_path.is_file():
        return [f"missing labels.json at {labels_path}"]
    if not manifest_path.is_file():
        return [f"missing labels manifest at {manifest_path}"]
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    missing = sorted(LABELS_MANIFEST_REQUIRED_KEYS - set(manifest))
    if missing:
        problems.append(f"labels manifest missing keys: {missing}")
    actual = _sha256_file(labels_path)
    if manifest.get("labels_sha256") != actual:
        problems.append("labels manifest labels_sha256 does not match labels.json bytes")
    labels: dict[str, Any] = json.loads(labels_path.read_text(encoding="utf-8"))
    if manifest.get("case_count") != len(labels.get("cases", [])):
        problems.append("labels manifest case_count does not match labels.json")
    if manifest.get("row_expectation_count") != len(labels.get("row_expectations", [])):
        problems.append("labels manifest row_expectation_count does not match labels.json")
    return problems
