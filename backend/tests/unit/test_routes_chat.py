"""Unit tests for the Home Chat route (/api/v1/chat/message)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, create_app
from app.voice.conversational_agent import _gather_live_financial_context


def test_chat_message_endpoint_success() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/message",
            json={
                "message": "What is the deterministic match rate and total variance?",
                "history": [],
                "page_context": {"tab": "home"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "reply" in data
        assert isinstance(data["reply"], str)
        assert len(data["reply"]) > 0
        assert data["latency_ms"] >= 0
        assert "context_summary" in data


def test_chat_message_empty_rejected() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat/message",
            json={
                "message": "",
                "history": [],
            },
        )
        assert response.status_code == 422


def test_live_context_reads_canonical_tables_for_latest_run(tmp_path: Path) -> None:
    isolated_app = create_app(
        Settings(db_path=tmp_path / "chat-context.sqlite3", ai_provider="none", _env_file=None)
    )
    with TestClient(isolated_app) as client:
        run_response = client.post(
            "/api/v1/runs/reconcile",
            json={"dataset_profile": "dev", "mode": "rules-only", "force": True},
        )
        assert run_response.status_code == 200
        run_summary = run_response.json()["summary"]

        context = _gather_live_financial_context(client.app.state.db)

    summary = context["summary"]
    assert summary["total_input_records"] == run_summary["raw_row_count"]
    assert summary["total_cases"] == run_summary["cases_count"]
    assert context["table_row_counts"]["norm_bank_entries"] > 0
    assert context["table_row_counts"]["norm_ledger_entries"] > 0
    assert context["recon_cases"]
    assert context["recon_cases"][0]["category"]
