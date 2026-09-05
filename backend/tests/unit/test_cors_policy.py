"""Browser-origin policy: safe defaults, explicit production origins, rejections.

The pre-hardening application reflected any ``Origin`` back with
``allow_credentials=True``. These tests pin the corrected boundary at both
levels: the pure policy resolver and the running ASGI application.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.cors import (
    DEFAULT_LOCAL_ORIGINS,
    CorsPolicyError,
    build_cors_policy,
    normalize_origin,
    parse_origin_list,
)
from app.main import create_app


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        db_path=tmp_path / "argus.sqlite3",
        import_staging_root=tmp_path / "staging",
        _env_file=None,
        **overrides,
    )


# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------


def test_default_policy_is_localhost_only_and_never_wildcard() -> None:
    policy = build_cors_policy("")
    assert policy.allow_origins == DEFAULT_LOCAL_ORIGINS
    assert policy.is_wildcard is False
    assert policy.source == "default-localhost"
    assert all(origin.startswith("http://") for origin in policy.allow_origins)
    assert "*" not in policy.allow_origins


def test_defaults_cover_the_dev_frontend_and_isolated_e2e_frontend_ports() -> None:
    origins = build_cors_policy(None).allow_origins
    # frontend dev/start port and frontend/playwright.config.ts default port.
    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins
    assert "http://localhost:3211" in origins
    assert "http://127.0.0.1:3211" in origins


# --------------------------------------------------------------------------
# Explicit production origins
# --------------------------------------------------------------------------


def test_explicit_production_origins_are_used_verbatim_after_normalization() -> None:
    policy = build_cors_policy("https://argus.example.com, https://ops.example.com:8443")
    assert policy.allow_origins == ("https://argus.example.com", "https://ops.example.com:8443")
    assert policy.allow_credentials is True
    assert policy.source == "explicit"
    # Configuring production origins must not silently keep the localhost ones.
    assert "http://localhost:3000" not in policy.allow_origins


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://ARGUS.Example.COM", "https://argus.example.com"),
        ("https://argus.example.com:443", "https://argus.example.com"),
        ("http://localhost:80", "http://localhost"),
        ("http://LocalHost:3000", "http://localhost:3000"),
        ("https://argus.example.com:8443", "https://argus.example.com:8443"),
        ("http://[::1]:3000", "http://[::1]:3000"),
    ],
)
def test_normalization_is_deterministic_and_does_not_broaden(raw: str, expected: str) -> None:
    assert normalize_origin(raw) == expected
    # Idempotent: normalizing the normal form changes nothing.
    assert normalize_origin(expected) == expected


def test_duplicates_collapse_deterministically_without_reordering() -> None:
    policy = build_cors_policy(
        "https://a.example.com,https://b.example.com,HTTPS://A.example.com:443"
    )
    assert policy.allow_origins == ("https://a.example.com", "https://b.example.com")


def test_parse_origin_list_accepts_a_sequence_and_a_comma_string() -> None:
    assert parse_origin_list(["https://a.example.com", "https://b.example.com"]) == (
        "https://a.example.com",
        "https://b.example.com",
    )
    assert parse_origin_list("  https://a.example.com , ,https://a.example.com ") == (
        "https://a.example.com",
    )
    assert parse_origin_list(None) == ()


def test_plaintext_public_origin_is_allowed_but_flagged_in_the_summary() -> None:
    policy = build_cors_policy("http://argus.internal.example.com")
    assert policy.allow_origins == ("http://argus.internal.example.com",)
    assert policy.source == "explicit-with-plaintext-origin"


# --------------------------------------------------------------------------
# Rejections
# --------------------------------------------------------------------------


def test_wildcard_with_credentials_is_rejected_not_downgraded() -> None:
    with pytest.raises(CorsPolicyError, match="credentialed"):
        build_cors_policy("*", allow_credentials=True)


def test_wildcard_is_permitted_only_without_credentials() -> None:
    policy = build_cors_policy("*", allow_credentials=False)
    assert policy.is_wildcard is True
    assert policy.allow_credentials is False


def test_wildcard_cannot_be_mixed_with_explicit_origins() -> None:
    with pytest.raises(CorsPolicyError, match="cannot be combined with explicit origins"):
        build_cors_policy("*,https://argus.example.com", allow_credentials=False)


@pytest.mark.parametrize(
    "bad",
    [
        "https://argus.example.com/app",  # path
        "https://argus.example.com/",  # trailing slash is a path
        "https://argus.example.com?x=1",  # query
        "https://argus.example.com#frag",  # fragment
        "https://user:pass@argus.example.com",  # credentials
        "ftp://argus.example.com",  # scheme
        "file:///etc/passwd",  # scheme
        "argus.example.com",  # no scheme
        "https://",  # no host
        "https://argus.example.com:notaport",  # invalid port
        "https://*.example.com",  # wildcard host
        "https://argus example.com",  # whitespace
        "",  # empty
        "*",  # wildcard is not a concrete origin
    ],
)
def test_malformed_origins_are_rejected(bad: str) -> None:
    with pytest.raises(CorsPolicyError):
        normalize_origin(bad)


def test_a_single_bad_origin_fails_the_whole_policy() -> None:
    with pytest.raises(CorsPolicyError, match="must not contain a path"):
        build_cors_policy("https://good.example.com,https://bad.example.com/app")


def test_settings_reject_an_invalid_origin_at_configuration_time(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="ARGUS_CORS_ALLOWED_ORIGINS is invalid"):
        _settings(tmp_path, cors_allowed_origins="https://argus.example.com/app")
    with pytest.raises(ValidationError, match="ARGUS_CORS_ALLOWED_ORIGINS is invalid"):
        _settings(tmp_path, cors_allowed_origins="*")


# --------------------------------------------------------------------------
# Safe summary
# --------------------------------------------------------------------------


def test_safe_summary_exposes_origin_state_and_no_secret(tmp_path: Path) -> None:
    settings = _settings(tmp_path, cors_allowed_origins="https://argus.example.com")
    summary = settings.safe_summary()
    assert summary["cors_allowed_origins"] == ["https://argus.example.com"]
    assert summary["cors_allow_credentials"] is True
    assert summary["cors_wildcard"] is False
    assert summary["cors_origin_source"] == "explicit"
    assert "token" not in repr(summary).lower()


# --------------------------------------------------------------------------
# Live CORS behaviour through the application
# --------------------------------------------------------------------------


def test_disallowed_origin_gets_no_allow_origin_header(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/v1/health", headers={"Origin": "https://evil.example.com"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_default_localhost_origin_is_allowed_with_credentials(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_preflight_is_refused_for_a_disallowed_origin(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_configured_production_origin_is_allowed_by_the_running_app(tmp_path: Path) -> None:
    settings = _settings(tmp_path, cors_allowed_origins="https://argus.example.com")
    with TestClient(create_app(settings)) as client:
        allowed = client.get("/api/v1/health", headers={"Origin": "https://argus.example.com"})
        rejected = client.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})
    assert allowed.headers["access-control-allow-origin"] == "https://argus.example.com"
    assert "access-control-allow-origin" not in {k.lower() for k in rejected.headers}


def test_no_origin_header_still_works_so_the_next_proxy_is_unaffected(tmp_path: Path) -> None:
    # The Next.js /api rewrite is a server-to-server call with no Origin.
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
