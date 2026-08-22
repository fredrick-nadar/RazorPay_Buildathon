"""Configuration behaviour: safe defaults, rules-only startup, useful failures."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_load_without_any_environment() -> None:
    settings = Settings()
    assert settings.db_path == type(settings).model_fields["db_path"].default
    assert settings.port == 8000
    assert settings.host == "127.0.0.1"
    assert settings.model_provider is None
    assert settings.model_api_key is None
    assert settings.rules_only is True


def test_missing_model_key_still_allows_rules_only_startup() -> None:
    # A provider name without a key must degrade to rules-only, never fail startup.
    settings = Settings(model_provider="demo-provider")
    assert settings.model_provider == "demo-provider"
    assert settings.rules_only is True


def test_configured_model_disables_rules_only_mode() -> None:
    settings = Settings(model_provider="demo-provider", model_api_key="dummy")
    assert settings.rules_only is False


def test_invalid_port_fails_with_useful_message() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(port=99999)
    assert "port" in str(excinfo.value)


def test_invalid_log_level_fails_with_useful_message() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(log_level="LOUD")
    assert "log_level" in str(excinfo.value)


def test_invalid_environment_variable_fails_with_useful_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_PORT", "not-an-integer")
    with pytest.raises(ValidationError) as excinfo:
        Settings()
    message = str(excinfo.value).lower()
    assert "port" in message


def test_safe_summary_never_contains_key_value() -> None:
    settings = Settings(model_provider="demo-provider", model_api_key="dummy-key")
    summary = settings.safe_summary()
    assert "dummy-key" not in str(summary)
    assert summary["model_key_configured"] is True
    assert summary["rules_only"] is False
