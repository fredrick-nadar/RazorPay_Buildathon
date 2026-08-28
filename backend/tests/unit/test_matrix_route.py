"""Regression tests for the paise-only master matrix API contract."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_matrix_returns_money_as_integer_paise_only(tmp_path: Path) -> None:
    app = create_app(Settings(db_path=tmp_path / "matrix.sqlite3", _env_file=None))
    with TestClient(app) as client:
        run_response = client.post(
            "/api/v1/runs/reconcile",
            json={"dataset_profile": "dev", "mode": "rules-only", "force": True},
        )
        run_id = run_response.json()["run_id"]
        response = client.get(f"/api/v1/runs/{run_id}/matrix?limit=10")
        client.app.state.db.execute(
            "INSERT INTO norm_payments "
            "(run_id, payment_id, source_row_number, content_hash, order_id, status, currency, "
            "gross_amount_paise, fee_paise, tax_paise, captured_at_utc, settlement_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                "pay_unmatched_matrix_test",
                999_999,
                "unmatched-content-hash",
                "order_unmatched_matrix_test",
                "CAPTURED",
                "INR",
                12_345,
                247,
                44,
                "2026-03-05T12:00:00Z",
                None,
            ),
        )
        with_unmatched_response = client.get(f"/api/v1/runs/{run_id}/matrix?limit=200")

    assert response.status_code == 200
    records = response.json()["records"]
    assert records
    assert with_unmatched_response.json()["total"] == response.json()["total"]
    assert all(
        record["payment_id"] != "pay_unmatched_matrix_test"
        for record in with_unmatched_response.json()["records"]
    )
    money_fields = {
        "gross_amount_paise",
        "fee_paise",
        "tax_paise",
        "net_amount_paise",
        "settlement_gross_paise",
        "bank_amount_paise",
        "ledger_amount_paise",
    }
    for record in records:
        assert not {"gross_amount", "fee_amount", "tax_amount", "net_amount"} & record.keys()
        for field in money_fields:
            assert record[field] is None or type(record[field]) is int
