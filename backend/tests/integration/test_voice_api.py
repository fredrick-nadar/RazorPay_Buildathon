"""Integration tests for the Voice Control API (PRD 13.5).

End-to-end: /languages honesty, /parse -> /execute lifecycle, confirmation
gate for state-changing-but-safe intents, forbidden refusals, and token
forgery rejection - all through the FastAPI app like a real client.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _client(tmp_path: Path) -> TestClient:
    app = create_app(Settings(db_path=tmp_path / "voice_api.sqlite3"))
    return TestClient(app)


def _seed_run(client: TestClient) -> None:
    response = client.post(
        "/api/v1/runs/reconcile",
        json={"dataset_profile": "dev", "mode": "rules-only", "force": True},
    )
    assert response.status_code == 200


def test_languages_report_honest_capability_tiers(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/v1/voice/languages")
        assert response.status_code == 200
        payload = response.json()
        by_code = {entry["code"]: entry["status"] for entry in payload["languages"]}
        assert by_code["en-IN"] == "ARGUS_TESTED"
        assert by_code["hi-IN"] == "ARGUS_TESTED"
        assert by_code["ta-IN"] == "AVAILABLE_FROM_PROVIDER"
        assert by_code["te-IN"] == "AVAILABLE_FROM_PROVIDER"
        assert by_code["kn-IN"] == "AVAILABLE_FROM_PROVIDER"
        assert "never approve" in payload["policy"]


def test_parse_then_execute_lists_unresolved_cases(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_run(client)
        parsed = client.post(
            "/api/v1/voice/parse",
            json={"transcript": "show unresolved cases", "language": "en-IN"},
        )
        assert parsed.status_code == 200
        parse_payload = parsed.json()
        assert parse_payload["status"] == "OK"
        assert parse_payload["intent"] == "LIST_UNRESOLVED_CASES"
        assert parse_payload["requires_confirmation"] is False
        assert parse_payload["token"]

        executed = client.post("/api/v1/voice/execute", json={"token": parse_payload["token"]})
        assert executed.status_code == 200
        result = executed.json()
        assert result["status"] == "EXECUTED"
        assert result["intent"] == "LIST_UNRESOLVED_CASES"
        assert isinstance(result["cases"], list)
        assert all(case["status"] == "UNRESOLVED" for case in result["cases"])
        assert result["navigation"]["type"] == "filter_cases"


def test_confirmation_gate_for_run_reconciliation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_run(client)
        parsed = client.post("/api/v1/voice/parse", json={"transcript": "run reconciliation"})
        payload = parsed.json()
        assert payload["requires_confirmation"] is True

        blocked = client.post("/api/v1/voice/execute", json={"token": payload["token"]})
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "REQUIRES_CONFIRMATION"

        confirmed = client.post(
            "/api/v1/voice/execute",
            json={"token": payload["token"], "confirmed": True},
        )
        assert confirmed.status_code == 200
        result = confirmed.json()
        assert result["status"] == "EXECUTED"
        assert result["run"]["run_id"].startswith("run-")


def test_forbidden_command_refused_with_audit(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_run(client)
        parsed = client.post("/api/v1/voice/parse", json={"transcript": "approve everything"})
        assert parsed.status_code == 200
        payload = parsed.json()
        assert payload["status"] == "REFUSED"
        assert payload["forbidden_intent"] == "APPROVE_CORRECTION"
        assert "approval panel" in payload["message"]
        # A refused parse carries no execution token at all.
        assert payload["token"] == ""

        executed = client.post("/api/v1/voice/execute", json={"token": "forged-token-abcdef"})
        assert executed.status_code == 200
        assert executed.json()["status"] == "ERROR"


def test_unknown_case_reference_resolves_honestly(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_run(client)
        parsed = client.post(
            "/api/v1/voice/parse",
            json={"transcript": "why is case-deadbeef1234 unresolved"},
        )
        assert parsed.json()["intent"] == "EXPLAIN_CASE"
        executed = client.post("/api/v1/voice/execute", json={"token": parsed.json()["token"]})
        result = executed.json()
        assert result["status"] == "NOT_FOUND"
        assert "no case matches" in result["message"].lower()


def test_explain_case_returns_briefing(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_run(client)
        cases = client.get("/api/v1/runs").json()[0]
        run_cases = client.get(f"/api/v1/runs/{cases['run_id']}/cases").json()
        unresolved = next(c for c in run_cases if c["status"] == "UNRESOLVED")

        parsed = client.post(
            "/api/v1/voice/parse",
            json={"transcript": f"why is {unresolved['case_id']} unresolved"},
        )
        executed = client.post("/api/v1/voice/execute", json={"token": parsed.json()["token"]})
        result = executed.json()
        assert result["status"] == "EXECUTED"
        assert result["briefing"] is not None
        assert unresolved["case_id"] in result["message"]
        assert result["navigation"]["case_id"] == unresolved["case_id"]


def test_transcript_length_is_capped(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post("/api/v1/voice/parse", json={"transcript": "x" * 500})
        assert response.status_code == 422  # Pydantic max_length guard


def test_command_atomic_fast_path(tmp_path: Path) -> None:
    """One round trip: parse + guard + execute in /voice/command."""
    with _client(tmp_path) as client:
        _seed_run(client)
        response = client.post(
            "/api/v1/voice/command",
            json={"transcript": "show unresolved cases", "language": "en-IN"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "EXECUTED"
        assert payload["intent"] == "LIST_UNRESOLVED_CASES"
        assert payload["execution"] is not None
        assert payload["execution"]["intent"] == "LIST_UNRESOLVED_CASES"


def test_command_refusal_is_atomic(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_run(client)
        response = client.post("/api/v1/voice/command", json={"transcript": "approve everything"})
        payload = response.json()
        assert payload["status"] == "REFUSED"
        assert payload["forbidden_intent"] == "APPROVE_CORRECTION"
        assert payload["execution"] is None
        assert payload["token"] == ""


def test_command_confirmation_gate(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_run(client)
        blocked = client.post("/api/v1/voice/command", json={"transcript": "run reconciliation"})
        payload = blocked.json()
        assert payload["status"] == "OK"
        assert payload["requires_confirmation"] is True
        assert payload["execution"] is None

        confirmed = client.post(
            "/api/v1/voice/command",
            json={"transcript": "run reconciliation", "confirmed": True},
        )
        result = confirmed.json()
        assert result["status"] == "EXECUTED"
        assert result["execution"]["run"]["run_id"].startswith("run-")


def test_transcribe_unconfigured_returns_501_fallback(tmp_path: Path) -> None:
    wav_b64 = (
        "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA="
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/voice/transcribe",
            json={
                "audio_base64": wav_b64,
                "language": "en-IN",
                "content_type": "audio/wav",
            },
        )
        assert response.status_code == 501
        payload = response.json()
        assert payload["status"] == "unavailable"
        assert payload["fallback"] == "browser"


def test_tts_unconfigured_returns_501_fallback(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/voice/tts",
            json={"text": "Three unresolved cases.", "language": "en-IN"},
        )
        assert response.status_code == 501
        assert response.json()["fallback"] == "browser"


def test_capabilities_reports_unconfigured_engines(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/v1/voice/capabilities")
        assert response.status_code == 200
        assert response.json() == {"stt": "unavailable", "tts": "unavailable"}


def test_brief_status_answers_batch_questions(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_run(client)
        response = client.post(
            "/api/v1/voice/command", json={"transcript": "how many cases are unresolved?"}
        )
        payload = response.json()
        assert payload["status"] == "EXECUTED"
        assert payload["message_key"] == "conversational_answer"
        assert "exception cases" in payload["message"]
        assert "variance" in payload["message"].lower()
