from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.importers.demo_settlement import DemoEvidenceError, build_demo_evidence
from app.main import create_app
from app.persistence.gateway_imports import GatewayEntity, persist_gateway_snapshot


def _rows(content: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content)))


def _payment(**changes: object) -> dict[str, object]:
    return {
        "id": "pay_boundary",
        "order_id": "order_boundary",
        "status": "captured",
        "amount": 10000,
        "fee": 236,
        "tax": 36,
        "currency": "INR",
        "created_at": 1772437000,
        **changes,
    }


def _refund(**changes: object) -> dict[str, object]:
    return {
        "id": "rfnd_boundary",
        "payment_id": "pay_boundary",
        "status": "processed",
        "amount": 1000,
        "currency": "INR",
        "created_at": 1772437100,
        **changes,
    }


def test_demo_refund_accounting_is_explicit_and_window_bounded() -> None:
    bundle = build_demo_evidence(
        import_id="gwi-boundary",
        payments=[_payment()],
        refunds=[
            _refund(),
            _refund(id="rfnd_failed", status="failed"),
            _refund(id="rfnd_unmatched", payment_id="pay_elsewhere"),
            _refund(id="rfnd_late", created_at=1772437000 + 86_401),
        ],
    )
    assert bundle["input_counts"] == {
        "payments_received": 1,
        "captured_payments_included": 1,
        "refunds_received": 4,
        "processed_refunds_included": 1,
        "refunds_excluded": 3,
    }
    assert [item["reason"] for item in bundle["refund_exclusions"]] == [
        "STATUS_NOT_PROCESSED",
        "PAYMENT_NOT_CAPTURED_IN_IMPORT",
        "OUTSIDE_SYNTHETIC_SETTLEMENT_WINDOW",
    ]
    assert len(_rows(bundle["files"]["refunds.csv"])) == 1
    assert bundle["synthetic_policy"]["policy_id"] == "argus-demo-settlement-v1"
    assert "not Razorpay" in bundle["synthetic_policy"]["notice"]


@pytest.mark.parametrize(
    ("payments", "refunds", "message"),
    [
        ([_payment(amount=-1)], [], "negative monetary"),
        ([_payment(fee=None)], [], "payment.fee"),
        ([_payment(tax=None)], [], "payment.tax"),
        ([_payment(created_at=10**30)], [], "UTC epoch"),
        ([_payment()], [_refund(amount=-1)], "positive amount"),
        ([_payment()], [_refund(currency="USD")], "currency differs"),
        ([_payment()], [_refund(amount=10001)], "exceed payment"),
        ([_payment()], [_refund(), _refund()], "duplicate IDs"),
    ],
)
def test_demo_rejects_unsafe_financial_boundaries(
    payments: list[dict[str, object]], refunds: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(DemoEvidenceError, match=message):
        build_demo_evidence(import_id="gwi-invalid", payments=payments, refunds=refunds)


def test_single_payment_batch_is_valid_and_deterministic() -> None:
    first = build_demo_evidence(import_id="gwi-single", payments=[_payment()], refunds=[])
    second = build_demo_evidence(import_id="gwi-single", payments=[_payment()], refunds=[])
    assert first == second
    assert first["settlements_count"] == 1


def test_demo_evidence_is_deterministic_conservative_and_labelled() -> None:
    payments = [
        {
            "id": "pay_test_1",
            "order_id": "order_test_1",
            "status": "captured",
            "amount": 10000,
            "fee": 236,
            "tax": 36,
            "currency": "INR",
            "created_at": 1772437000,
        },
        {
            "id": "pay_test_2",
            "order_id": "order_test_2",
            "status": "captured",
            "amount": 20000,
            "fee": 472,
            "tax": 72,
            "currency": "INR",
            "created_at": 1772438000,
        },
    ]
    refunds = [
        {
            "id": "rfnd_test_1",
            "payment_id": "pay_test_1",
            "status": "processed",
            "amount": 1000,
            "currency": "INR",
            "created_at": 1772439000,
        }
    ]

    first = build_demo_evidence(
        import_id="gwi-test", payments=payments, refunds=refunds, include_merchant_sources=True
    )
    second = build_demo_evidence(
        import_id="gwi-test", payments=payments, refunds=refunds, include_merchant_sources=True
    )
    gateway = build_demo_evidence(import_id="gwi-test", payments=payments, refunds=refunds)
    assert gateway["scope"] == "GATEWAY_ONLY"
    assert set(gateway["files"]) == {"payments.csv", "refunds.csv", "settlements.csv"}
    assert gateway["bank_entries_count"] == gateway["ledger_entries_count"] == 0
    # Stable gateway bytes keep existing matching companion scenarios usable.
    assert all(content == first["files"][name] for name, content in gateway["files"].items())

    assert first == second
    settlements = _rows(first["files"]["settlements.csv"])
    bank = _rows(first["files"]["bank_entries.csv"])
    payment_rows = _rows(first["files"]["payments.csv"])
    ledger = _rows(first["files"]["ledger_entries.csv"])
    assert len(settlements) == 1
    assert settlements[0]["gross_credit"] == "300.00"
    assert settlements[0]["fee_amount"] == "6.00"
    assert settlements[0]["tax_amount"] == "1.08"
    assert settlements[0]["adjustment_amount"] == "-10.00"
    assert settlements[0]["net_amount"] == "282.92"
    assert settlements[0]["utr"].startswith("DEMO")
    assert bank[0]["signed_amount"] == "282.92"
    assert "SYNTHETIC_DEMO" in bank[0]["narration"]
    assert {row["settlement_id"] for row in payment_rows} == {settlements[0]["settlement_id"]}
    assert all(row["entry_origin"] == "IMPORTED" for row in ledger)
    assert all("SYNTHETIC_DEMO" in row["description"] for row in ledger)


def test_demo_evidence_endpoint_requires_separate_merchant_uploads(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "demo.sqlite3",
        import_staging_root=tmp_path / "imports",
        _env_file=None,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        snapshot = persist_gateway_snapshot(
            app.state.db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="fixture-key",
            entities=[
                GatewayEntity(
                    entity_type="PAYMENT",
                    entity_id="pay_test_1",
                    payload={
                        "id": "pay_test_1",
                        "order_id": "order_test_1",
                        "status": "captured",
                        "amount": 10000,
                        "fee": 236,
                        "tax": 36,
                        "currency": "INR",
                        "created_at": 1772437000,
                    },
                    reconciliation_eligible=True,
                    readiness_state="AWAITING_RAZORPAY_SETTLEMENT",
                )
            ],
        )
        response = client.post(
            f"/api/v1/razorpay/imports/{snapshot.import_id}/generate-demo-evidence",
            json={"session_id": "demo_session"},
        )
        status = client.get("/api/v1/ingest/sessions/demo_session/status")
        reconciliation = client.post(
            "/api/v1/ingest/reconcile-session",
            json={"session_id": "demo_session", "mode": "rules-only"},
        )
        live_snapshot = persist_gateway_snapshot(
            app.state.db,
            provider="RAZORPAY",
            mode="LIVE",
            credential_identifier="fixture-live-key",
            entities=[],
        )
        live_attempt = client.post(
            f"/api/v1/razorpay/imports/{live_snapshot.import_id}/generate-demo-evidence",
            json={"session_id": "live_session"},
        )

    assert response.status_code == 200
    assert response.json()["provenance"] == "SYNTHETIC_DEMO"
    assert response.json()["production_eligible"] is False
    assert status.status_code == 200
    assert response.json()["scope"] == "GATEWAY_ONLY"
    assert status.json()["ready"] is False
    assert status.json()["ready_source_groups"] == 1
    assert status.json()["bank_ready"] is status.json()["ledger_ready"] is False
    assert set(status.json()["active_sources"]) == {"payments", "refunds", "settlements"}
    assert status.json()["lifecycle_state"] == "AWAITING_BANK_EVIDENCE"
    assert {item["origin"] for item in status.json()["active_sources"].values()} == {
        "SYNTHETIC_DEMO"
    }
    assert reconciliation.status_code == 409
    assert "bank statement" in reconciliation.json()["detail"]
    assert "merchant ledger" in reconciliation.json()["detail"]
    assert live_attempt.status_code == 409
    assert "disabled outside Razorpay Test Mode" in live_attempt.json()["detail"]


def test_reopened_session_restores_import_dossier_and_demo_provenance(tmp_path: Path) -> None:
    """A refresh must restore the linked import and its SYNTHETIC_DEMO label.

    The browser keeps no durable copy of an import. Everything the dialog shows
    after a reload has to be re-readable from the session status plus the
    gateway-import endpoint alone.
    """
    settings = Settings(
        db_path=tmp_path / "restore.sqlite3",
        import_staging_root=tmp_path / "imports",
        _env_file=None,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        snapshot = persist_gateway_snapshot(
            app.state.db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="fixture-key",
            entities=[
                GatewayEntity(
                    entity_type="ORDER",
                    entity_id="order_test_1",
                    payload={"id": "order_test_1", "status": "paid", "amount": 10000},
                    reconciliation_eligible=False,
                    exclusion_reason="ORDER_IS_NOT_A_PAYMENT",
                ),
                GatewayEntity(
                    entity_type="PAYMENT",
                    entity_id="pay_test_1",
                    payload={
                        "id": "pay_test_1",
                        "order_id": "order_test_1",
                        "status": "captured",
                        "amount": 10000,
                        "fee": 236,
                        "tax": 36,
                        "currency": "INR",
                        "created_at": 1772437000,
                    },
                    reconciliation_eligible=True,
                    readiness_state="AWAITING_RAZORPAY_SETTLEMENT",
                ),
            ],
        )
        demo = client.post(
            f"/api/v1/razorpay/imports/{snapshot.import_id}/generate-demo-evidence",
            json={"session_id": "restore_session"},
        )
        assert demo.status_code == 200, demo.text
        evidence_id = demo.json()["evidence_id"]

    # A second app over the same database stands in for an API restart, and a
    # client that never saw the sync response stands in for a browser refresh.
    restarted = create_app(
        Settings(
            db_path=tmp_path / "restore.sqlite3",
            import_staging_root=tmp_path / "imports",
            _env_file=None,
        )
    )
    with TestClient(restarted) as client:
        status = client.get("/api/v1/ingest/sessions/restore_session/status")
        assert status.status_code == 200, status.text
        session = status.json()
        # The session status is the only thing the reopened dialog starts from.
        assert session["gateway_import_id"] == snapshot.import_id
        assert session["active_sources"]["settlements"]["origin"] == "SYNTHETIC_DEMO"

        restored = client.get(
            f"/api/v1/razorpay/imports/{session['gateway_import_id']}",
            params={"session_id": "restore_session"},
        )
        assert restored.status_code == 200, restored.text
        detail = restored.json()
        # Everything the intake card renders is present without a re-import.
        assert detail["counts"]["ORDER"] == 1
        assert detail["counts"]["PAYMENT"] == 1
        assert detail["status"] == "CAPTURED"
        assert detail["source_records_count"] == 2
        assert detail["payment_dossier_total"] == 1
        assert detail["payment_dossier_truncated"] is False
        assert [item["payment_id"] for item in detail["payment_dossier"]] == ["pay_test_1"]
        # Provenance survives the restart in persisted state, not client memory.
        assert detail["demo_evidence"] is not None
        assert detail["demo_evidence"]["evidence_id"] == evidence_id
        assert detail["demo_evidence"]["provenance"] == "SYNTHETIC_DEMO"
        assert detail["demo_evidence"]["production_eligible"] is False

        # Without a session, the endpoint reports no session-scoped demo link.
        anonymous = client.get(f"/api/v1/razorpay/imports/{snapshot.import_id}")
        assert anonymous.status_code == 200, anonymous.text
        assert anonymous.json()["demo_evidence"] is None

        # Another session cannot inherit this session's demo provenance.
        other = client.get(
            f"/api/v1/razorpay/imports/{snapshot.import_id}",
            params={"session_id": "other_session"},
        )
        assert other.status_code == 200, other.text
        assert other.json()["demo_evidence"] is None

        # The dossier window is bounded at the API boundary too.
        assert (
            client.get(
                f"/api/v1/razorpay/imports/{snapshot.import_id}",
                params={"dossier_limit": 0},
            ).status_code
            == 422
        )
        assert (
            client.get(
                f"/api/v1/razorpay/imports/{snapshot.import_id}",
                params={"dossier_limit": 201},
            ).status_code
            == 422
        )
        assert (
            client.get(
                f"/api/v1/razorpay/imports/{snapshot.import_id}",
                params={"dossier_offset": -1},
            ).status_code
            == 422
        )
