"""Unit tests for the independent holdout variation transform (PRD 13.3)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from app.evaluation.dataset_spec import COLUMNS
from app.evaluation.holdout_variation import (
    apply_holdout_variation,
    economic_projection,
)
from app.importers.ingest import HEADER_ALIASES, IngestError, ingest_inputs

_HEADERS: dict[str, str] = {
    "payments.csv": (
        "payment_id,order_id,status,currency,gross_amount,fee_amount,tax_amount,"
        "captured_at_utc,settlement_id\n"
    ),
    "refunds.csv": (
        "refund_id,payment_id,status,currency,refund_amount,created_at_utc,settlement_id\n"
    ),
    "settlements.csv": (
        "settlement_id,settled_at_utc,window_start_utc,window_end_utc,status,currency,"
        "gross_credit,fee_amount,tax_amount,adjustment_amount,net_amount,utr\n"
    ),
    "bank_entries.csv": (
        "bank_entry_id,posted_at_utc,value_date,currency,signed_amount,narration,"
        "utr,account_fingerprint\n"
    ),
    "ledger_entries.csv": (
        "ledger_entry_id,account_code,accounting_date,currency,signed_amount,"
        "source_reference,source_type,description,entry_origin\n"
    ),
}


def _sample_rows() -> dict[str, list[dict[str, str]]]:
    payments = [
        {
            "payment_id": f"pay_{i:03d}",
            "order_id": f"order_{i:03d}" if i % 2 == 0 else "",
            "status": "CAPTURED",
            "currency": "INR",
            "gross_amount": f"{1000 + i}.00",
            "fee_amount": "20.00",
            "tax_amount": "4.00",
            "captured_at_utc": "2026-06-01T10:00:00Z",
            "settlement_id": "setl_001",
        }
        for i in range(12)
    ]
    rows: dict[str, list[dict[str, str]]] = {"payments": payments}
    for name in ("refunds", "settlements", "bank_entries", "ledger_entries"):
        rows[name] = []
    return rows


def test_variation_is_deterministic() -> None:
    rows = _sample_rows()
    first = apply_holdout_variation(9107, rows, COLUMNS)
    second = apply_holdout_variation(9107, rows, COLUMNS)
    assert first.rows == second.rows
    assert first.columns == second.columns


def test_variation_preserves_economics_exactly() -> None:
    rows = _sample_rows()
    before = economic_projection(rows, COLUMNS)
    varied = apply_holdout_variation(9107, rows, COLUMNS)
    after = economic_projection(varied.rows, COLUMNS)

    # Financial economics must be an exact permutation per file: identifiers,
    # amounts, currencies, statuses, timestamps, and references never change.
    for name in rows:

        def signature(row: dict[str, str]) -> tuple[tuple[str, str], ...]:
            return tuple(sorted((k, v) for k, v in row.items() if k != "order_id"))

        before_counter = Counter(signature(row) for row in before[name])
        after_counter = Counter(signature(row) for row in after[name])
        assert before_counter == after_counter, f"economics changed for {name}"

    # order_id (the intentionally varied optional field) may only be emptied,
    # never populated with a new value.
    original_by_payment = {row["payment_id"]: row.get("order_id", "") for row in before["payments"]}
    for row in after["payments"]:
        expected = original_by_payment[row["payment_id"]]
        assert row["order_id"] in (expected, "")


def test_variation_renames_harmless_columns_only() -> None:
    varied = apply_holdout_variation(9107, _sample_rows(), COLUMNS)
    assert "fee" in varied.columns["payments"]
    assert "tax" in varied.columns["payments"]
    assert "fee" in varied.columns["settlements"]
    # Non-payment/settlement files keep canonical headers.
    assert varied.columns["bank_entries"] == COLUMNS["bank_entries"]
    for row in varied.rows["payments"]:
        assert "fee_amount" not in row
        assert "fee" in row


def test_variation_shuffles_row_order() -> None:
    rows = _sample_rows()
    varied = apply_holdout_variation(9107, rows, COLUMNS)
    original_order = [row["payment_id"] for row in rows["payments"]]
    varied_order = [row["payment_id"] for row in varied.rows["payments"]]
    assert sorted(original_order) == sorted(varied_order)
    assert original_order != varied_order, "deterministic shuffle should change ordering"


def test_variation_empties_optional_order_id_subset() -> None:
    rows = _sample_rows()
    populated = sum(1 for row in rows["payments"] if row["order_id"])
    varied = apply_holdout_variation(9107, rows, COLUMNS)
    remaining = sum(1 for row in varied.rows["payments"] if row["order_id"])
    assert remaining < populated
    assert remaining > 0, "subset drop must not empty every optional field"


def test_ingest_resolves_variant_headers(tmp_path: Path) -> None:
    """The documented aliases ingest cleanly and map back to canonical names."""
    variant_inputs = tmp_path / "variant_inputs"
    variant_inputs.mkdir()

    for filename, header in _HEADERS.items():
        if filename == "payments.csv":
            variant_header = header.replace("fee_amount", "fee").replace("tax_amount", "tax")
            row = "pay_v01,ord_v01,CAPTURED,INR,5000,100,18,2026-03-02T10:00:00Z,setl_v01\n"
            (variant_inputs / filename).write_text(variant_header + row, encoding="utf-8")
        else:
            (variant_inputs / filename).write_text(header, encoding="utf-8")

    assert HEADER_ALIASES["fee"] == "fee_amount"
    result = ingest_inputs(variant_inputs)
    assert result.accepted_count == 1
    payment = result.records.payments[0]
    assert payment.gross_amount_paise == 500000
    assert payment.fee_paise == 10000
    assert payment.tax_paise == 1800


def test_ingest_still_rejects_unknown_columns(tmp_path: Path) -> None:
    """Aliases do not weaken the schema-drift contract."""
    drifted = tmp_path / "drifted"
    drifted.mkdir()
    for filename, header in _HEADERS.items():
        if filename == "payments.csv":
            (drifted / filename).write_text(header.rstrip("\n") + ",mystery\n", encoding="utf-8")
        else:
            (drifted / filename).write_text(header, encoding="utf-8")

    with pytest.raises(IngestError):
        ingest_inputs(drifted)
