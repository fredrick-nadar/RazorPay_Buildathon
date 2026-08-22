"""Health and version endpoint behaviour (PRD 12.1)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(db_path=tmp_path / "health-test.sqlite3")
    return TestClient(create_app(settings))


def test_health_returns_version_and_persistence_status(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str) and body["version"]
    assert body["persistence"]["backend"] == "sqlite"
    assert body["persistence"]["ok"] is True
    assert isinstance(body["persistence"]["schema_version"], int)


def test_health_reports_rules_only_startup_without_model_key(tmp_path: Path) -> None:
    # The app boots with zero model configuration; health stays green (Phase 0 gate).
    settings = Settings(db_path=tmp_path / "rules-only.sqlite3")
    assert settings.rules_only is True
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/health").status_code == 200


def test_version_endpoint_shape(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/api/v1/version")
    assert response.status_code == 200
    body = response.json()
    assert body["app_name"] == "ARGUS CONTROL"
    assert body["api_version"] == "v1"
    assert isinstance(body["app_version"], str) and body["app_version"]
    assert isinstance(body["domain_contract_version"], str) and body["domain_contract_version"]


def test_database_file_created_under_configured_path(tmp_path: Path) -> None:
    db_file = tmp_path / "created.sqlite3"
    settings = Settings(db_path=db_file)
    with TestClient(create_app(settings)):
        pass
    assert db_file.is_file()
