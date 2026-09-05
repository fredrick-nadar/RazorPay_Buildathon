"""Regression tests for the paise-only master matrix API contract."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

MONEY_FIELDS = {
    "signed_amount_paise",
    "gross_amount_paise",
    "fee_paise",
    "tax_paise",
    "net_amount_paise",
    "settlement_gross_paise",
    "bank_amount_paise",
    "ledger_amount_paise",
    "refund_amount_paise",
    "gross_credit_paise",
    "adjustment_paise",
}


def _reconcile(client: TestClient) -> str:
    response = client.post(
        "/api/v1/runs/reconcile",
        json={"dataset_profile": "dev", "mode": "rules-only", "force": True},
    )
    assert response.status_code == 200
    return str(response.json()["run_id"])


def test_matrix_returns_money_as_integer_paise_only(tmp_path: Path) -> None:
    app = create_app(Settings(db_path=tmp_path / "matrix.sqlite3", _env_file=None))
    with TestClient(app) as client:
        run_id = _reconcile(client)
        response = client.get(f"/api/v1/runs/{run_id}/matrix?limit=200")

    assert response.status_code == 200
    records = response.json()["records"]
    assert records
    for record in records:
        assert not {"gross_amount", "fee_amount", "tax_amount", "net_amount"} & record.keys()
        for field in MONEY_FIELDS & record.keys():
            assert record[field] is None or type(record[field]) is int


def test_matrix_reports_every_record_type_and_its_link_state(tmp_path: Path) -> None:
    """The matrix is the run's whole inventory, not only fully linked payments.

    Chunk 3C: the endpoint used to anchor on payments and skip any row without
    a complete chain, and never read refunds at all, so most of the run was
    invisible while the UI labelled the remainder the reconciled matrix.
    """
    app = create_app(Settings(db_path=tmp_path / "inventory.sqlite3", _env_file=None))
    with TestClient(app) as client:
        run_id = _reconcile(client)
        matrix = client.get(f"/api/v1/runs/{run_id}/matrix?limit=200").json()
        summary = client.get(f"/api/v1/runs/{run_id}/summary").json()

        counts = {
            kind: client.app.state.db.query_one(
                f"SELECT COUNT(*) AS c FROM {table} WHERE run_id = ?",  # noqa: S608
                (run_id,),
            )["c"]
            for kind, table in (
                ("PAYMENT", "norm_payments"),
                ("REFUND", "norm_refunds"),
                ("SETTLEMENT", "norm_settlements"),
                ("BANK_ENTRY", "norm_bank_entries"),
                ("LEDGER_ENTRY", "norm_ledger_entries"),
            )
        }

    census = matrix["inventory"]["by_record_type"]
    for kind, expected in counts.items():
        assert census[kind]["total"] == expected, kind
        assert census[kind]["reconciled"] + census[kind]["unmatched"] == expected

    # Every accepted record is accounted for exactly once.
    assert matrix["inventory"]["total_records"] == sum(counts.values())
    assert matrix["inventory"]["total_records"] == summary["summary"]["eligible_record_count"]
    assert (
        matrix["inventory"]["reconciled_records"] + matrix["inventory"]["unmatched_records"]
        == matrix["inventory"]["total_records"]
    )
    assert matrix["total"] == matrix["inventory"]["total_records"]
    # Refunds are present as their own record type; they used to be absent.
    assert census["REFUND"]["total"] > 0


def test_matrix_keeps_an_unmatched_payment_visible(tmp_path: Path) -> None:
    """An unmatched row is reported as UNMATCHED, never dropped."""
    app = create_app(Settings(db_path=tmp_path / "unmatched.sqlite3", _env_file=None))
    with TestClient(app) as client:
        run_id = _reconcile(client)
        before = client.get(f"/api/v1/runs/{run_id}/matrix?limit=200").json()
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
        after = client.get(f"/api/v1/runs/{run_id}/matrix?limit=200").json()
        found = client.get(
            f"/api/v1/runs/{run_id}/matrix?limit=200&search=pay_unmatched_matrix_test"
        ).json()
        unmatched_only = client.get(
            f"/api/v1/runs/{run_id}/matrix?limit=200&link_state=UNMATCHED"
        ).json()

    assert after["inventory"]["total_records"] == before["inventory"]["total_records"] + 1
    assert after["inventory"]["unmatched_records"] == before["inventory"]["unmatched_records"] + 1
    # The reconciled population is unchanged: an unmatched row is never counted
    # as reconciled just because it is now visible.
    assert after["inventory"]["reconciled_records"] == before["inventory"]["reconciled_records"]

    added = found["records"]
    assert len(added) == 1
    assert added[0]["link_state"] == "UNMATCHED"
    assert "NO_MATCH_GROUP" in added[0]["missing_links"]
    assert "NO_SETTLEMENT" in added[0]["missing_links"]
    assert any(
        record["record_id"] == "pay_unmatched_matrix_test" for record in unmatched_only["records"]
    )
    assert all(record["link_state"] == "UNMATCHED" for record in unmatched_only["records"])


def test_matrix_filters_by_record_type(tmp_path: Path) -> None:
    app = create_app(Settings(db_path=tmp_path / "filtered.sqlite3", _env_file=None))
    with TestClient(app) as client:
        run_id = _reconcile(client)
        refunds = client.get(f"/api/v1/runs/{run_id}/matrix?limit=200&record_type=REFUND").json()
        rejected = client.get(f"/api/v1/runs/{run_id}/matrix?record_type=ORDER")
        bad_state = client.get(f"/api/v1/runs/{run_id}/matrix?link_state=MAYBE")

    assert refunds["record_type"] == "REFUND"
    assert refunds["records"]
    assert all(record["record_type"] == "REFUND" for record in refunds["records"])
    # A refund reduces merchant receipts, so it is carried signed negative.
    assert all(record["signed_amount_paise"] <= 0 for record in refunds["records"])
    # The census always describes the whole run, not the filtered slice.
    assert refunds["inventory"]["by_record_type"]["PAYMENT"]["total"] > 0
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "UNKNOWN_RECORD_TYPE"
    assert bad_state.status_code == 400
    assert bad_state.json()["detail"] == "UNKNOWN_LINK_STATE"


def test_matrix_match_identity_is_typed_and_ambiguous_links_stay_unmatched(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(db_path=tmp_path / "typed-links.sqlite3", _env_file=None))
    with TestClient(app) as client:
        run_id = _reconcile(client)
        db = client.app.state.db
        payment = db.query_one(
            "SELECT p.payment_id, p.settlement_id, s.utr "
            "FROM norm_payments p "
            "JOIN norm_settlements s ON s.run_id = p.run_id AND s.settlement_id = p.settlement_id "
            "JOIN match_members mm ON mm.record_type = 'PAYMENT' "
            "AND mm.record_id = p.payment_id "
            "JOIN match_groups mg ON mg.match_id = mm.match_id AND mg.run_id = p.run_id "
            "WHERE p.run_id = ? AND s.utr IS NOT NULL LIMIT 1",
            (run_id,),
        )
        assert payment is not None
        payment_id = str(payment["payment_id"])
        settlement_id = str(payment["settlement_id"])
        utr = str(payment["utr"])

        # Same text id, different record type: the PAYMENT match must not make
        # this REFUND look matched.
        db.execute(
            "INSERT INTO norm_refunds "
            "(run_id, refund_id, source_row_number, content_hash, payment_id, status, currency, "
            "refund_amount_paise, created_at_utc, settlement_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                payment_id,
                999_001,
                "a" * 64,
                payment_id,
                "processed",
                "INR",
                100,
                "2026-08-24T00:00:00+00:00",
                settlement_id,
            ),
        )
        refund = client.get(
            f"/api/v1/runs/{run_id}/matrix?record_type=REFUND&search={payment_id}"
        ).json()["records"][0]

        # A second bank row with the same UTR is non-unique evidence. The
        # matrix must not choose one arbitrarily and call the chain reconciled.
        db.execute(
            "INSERT INTO norm_bank_entries "
            "(run_id, bank_entry_id, source_row_number, content_hash, posted_at_utc, value_date, "
            "currency, signed_amount_paise, narration, utr, account_fingerprint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                "bnk-ambiguous-matrix",
                999_002,
                "b" * 64,
                "2026-08-24T00:00:00+00:00",
                "2026-08-24",
                "INR",
                100,
                "synthetic duplicate UTR",
                utr,
                "synthetic-account",
            ),
        )
        payment_row = client.get(
            f"/api/v1/runs/{run_id}/matrix?record_type=PAYMENT&search={payment_id}"
        ).json()["records"][0]

    assert refund["link_state"] == "UNMATCHED"
    assert "NO_MATCH_GROUP" in refund["missing_links"]
    assert payment_row["link_state"] == "UNMATCHED"
    assert payment_row["bank_entry_id"] is None
    assert "NON_UNIQUE_BANK_ENTRY" in payment_row["missing_links"]


def test_matrix_fails_closed_for_an_unknown_run(tmp_path: Path) -> None:
    """A missing run is an error, not a legitimately empty matrix."""
    app = create_app(Settings(db_path=tmp_path / "missing.sqlite3", _env_file=None))
    with TestClient(app) as client:
        response = client.get("/api/v1/runs/run-does-not-exist/matrix")

    assert response.status_code == 404


@pytest.mark.parametrize("profile", ["holdout", "adversarial"])
def test_matrix_inventory_matches_run_eligibility_outside_dev(tmp_path: Path, profile: str) -> None:
    app = create_app(Settings(db_path=tmp_path / f"{profile}.sqlite3", _env_file=None))
    with TestClient(app) as client:
        reconciled = client.post(
            "/api/v1/runs/reconcile",
            json={"dataset_profile": profile, "mode": "rules-only", "force": True},
        )
        assert reconciled.status_code == 200
        run_id = reconciled.json()["run_id"]
        matrix = client.get(f"/api/v1/runs/{run_id}/matrix?limit=10").json()

    assert (
        matrix["inventory"]["total_records"]
        == reconciled.json()["summary"]["eligible_record_count"]
    )
