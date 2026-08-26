"""Unit tests for the Home Chat route (/api/v1/chat/message)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


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
