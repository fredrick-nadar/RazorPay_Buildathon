"""Unit tests for Multi-Source CSV upload and validation endpoints."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_csv_upload_and_reconciliation(tmp_path: Path) -> None:
    settings = Settings(ARGUS_DB_PATH=str(tmp_path / "test_csv_ingest.db"))
    app = create_app(settings)

    with TestClient(app) as client:
        # 1. Upload sample payments CSV matching format
        sample_csv = (
            "payment_id,order_id,status,currency,gross_amount,fee_amount,tax_amount,captured_at_utc,settlement_id\n"
            "pay_test_001,ord_001,CAPTURED,INR,1000.00,20.00,3.60,2026-03-02T03:17:28Z,stl_xhb67rhUhk\n"
            "pay_test_002,ord_002,CAPTURED,INR,2500.00,50.00,9.00,2026-03-02T03:50:39Z,stl_xhb67rhUhk\n"
        )

        res = client.post(
            "/api/v1/ingest/upload-csv",
            json={
                "filename": "payments.csv",
                "content": sample_csv,
                "file_type": "payments",
                "session_id": "test_session_1",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["file_type"] == "payments"
        assert data["rows_count"] == 2
        assert "checksum_sha256" in data
        assert len(data["preview_rows"]) == 2

        # 2. Reconcile uploaded session
        rec_res = client.post(
            "/api/v1/ingest/reconcile-session",
            json={"session_id": "test_session_1", "fallback_profile": "dev", "mode": "rules-only"},
        )
        assert rec_res.status_code == 200
        rec_data = rec_res.json()
        assert "run_id" in rec_data
        assert rec_data["status"] == "COMPLETED"


def test_empty_csv_upload_rejected(tmp_path: Path) -> None:
    settings = Settings(ARGUS_DB_PATH=str(tmp_path / "test_empty_csv.db"))
    app = create_app(settings)

    with TestClient(app) as client:
        res = client.post(
            "/api/v1/ingest/upload-csv",
            json={
                "filename": "empty.csv",
                "content": "   ",
                "file_type": "payments",
                "session_id": "test_empty",
            },
        )
        assert res.status_code == 400


def test_pdf_and_image_document_extraction(tmp_path: Path) -> None:
    import base64

    settings = Settings(ARGUS_DB_PATH=str(tmp_path / "test_doc_extract.db"))
    app = create_app(settings)

    with TestClient(app) as client:
        # Simulate base64 image/PDF document
        fake_content = "PDF-1.4 Bank Statement HDFC Bank UTR_992100 pay_9901 ₹12,500.00".encode()
        b64_content = base64.b64encode(fake_content).decode("utf-8")

        res = client.post(
            "/api/v1/ingest/upload-document",
            json={
                "filename": "hdfc_bank_statement.pdf",
                "content_base64": b64_content,
                "mime_type": "application/pdf",
                "session_id": "test_doc_session",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert "rows_count" in data
        assert data["rows_count"] > 0
        assert "extractor" in data
        assert data["status"] == "VALIDATED"


def test_non_financial_document_rejected(tmp_path: Path) -> None:
    import base64

    settings = Settings(ARGUS_DB_PATH=str(tmp_path / "test_non_fin.db"))
    app = create_app(settings)

    with TestClient(app) as client:
        # Simulate unrelated non-financial document (e.g. recipe / poem)
        fake_content = b"The quick brown fox jumps over the lazy dog in the meadow."
        b64_content = base64.b64encode(fake_content).decode("utf-8")

        res = client.post(
            "/api/v1/ingest/upload-document",
            json={
                "filename": "poem.pdf",
                "content_base64": b64_content,
                "mime_type": "application/pdf",
                "session_id": "test_non_fin_session",
            },
        )
        assert res.status_code == 400
        err = res.json()
        assert "no recognizable financial" in err["detail"].lower()
