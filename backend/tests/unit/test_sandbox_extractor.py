"""Unit tests for Python Sandbox Streaming Extractor and Live Verification."""

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.importers.sandbox_runner import run_sandbox_extraction_stream
from app.main import create_app


def test_sandbox_stream_generator_csv() -> None:
    csv_data = (
        "payment_id,order_id,status,currency,gross_amount,fee_amount,"
        "tax_amount,captured_at_utc,settlement_id\n"
        "pay_SANDBOX_01,ord_01,CAPTURED,INR,1500.00,30.00,5.40,2026-03-02T10:00:00Z,stl_DEMO_01\n"
        "pay_SANDBOX_02,ord_02,CAPTURED,INR,4200.00,84.00,15.12,2026-03-02T11:00:00Z,stl_DEMO_01\n"
    )

    events = list(
        run_sandbox_extraction_stream(
            filename="payments_test.csv",
            raw_content=csv_data,
            mime_type="text/csv",
            session_id="test_stream_session",
        )
    )

    assert len(events) > 0
    event_types = []
    for evt in events:
        assert evt.startswith("data: ")
        data = json.loads(evt.replace("data: ", "").strip())
        event_types.append(data.get("type"))

    assert "task_init" in event_types
    assert "task_update" in event_types
    assert "code_ready" in event_types
    assert "stdout" in event_types
    assert "complete" in event_types


def test_sandbox_stream_endpoint(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "test_sandbox_stream.db")
    app = create_app(settings)

    with TestClient(app) as client:
        csv_data = (
            "settlement_id,gross_credit,fee_amount,tax_amount,net_amount,settled_at_utc,utr\n"
            "stl_TEST_001,10000.00,200.00,36.00,9764.00,2026-03-02T10:00:00Z,UTR_RZP_001\n"
        )

        res = client.post(
            "/api/v1/ingest/stream-extract",
            json={
                "filename": "settlements.csv",
                "content": csv_data,
                "mime_type": "text/csv",
                "session_id": "test_stream_api",
            },
        )
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]

        lines = res.text.split("\n\n")
        parsed_events = [
            json.loads(line.replace("data: ", ""))
            for line in lines
            if line.strip().startswith("data: ")
        ]
        assert len(parsed_events) >= 5

        complete_event = next((e for e in parsed_events if e.get("type") == "complete"), None)
        assert complete_event is not None
        assert complete_event["result"]["status"] == "VALIDATED"
        assert complete_event["result"]["mapped_filename"] == "settlements.csv"

        commit_res = client.post(
            "/api/v1/ingest/commit-extracted",
            json={
                "session_id": "test_stream_api",
                "target_filename": "settlements.csv",
                "canonical_csv": complete_event["result"]["canonical_csv"],
            },
        )
        assert commit_res.status_code == 200
        assert commit_res.json()["status"] == "COMMITTED"


def test_sandbox_stream_pdf_document(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "test_sandbox_pdf.db")
    app = create_app(settings)

    with TestClient(app) as client:
        fake_pdf = "PDF-1.4 HDFC Bank Statement UTR_9921 pay_9901 ₹12,500.00".encode()
        b64_content = base64.b64encode(fake_pdf).decode("utf-8")

        res = client.post(
            "/api/v1/ingest/stream-extract",
            json={
                "filename": "hdfc_bank_statement.pdf",
                "content_base64": b64_content,
                "mime_type": "application/pdf",
                "session_id": "test_pdf_stream",
            },
        )
        assert res.status_code == 200
        lines = res.text.split("\n\n")
        parsed_events = [
            json.loads(line.replace("data: ", ""))
            for line in lines
            if line.strip().startswith("data: ")
        ]
        assert any(e.get("type") == "code_ready" for e in parsed_events)
        assert any(e.get("type") == "complete" for e in parsed_events)
