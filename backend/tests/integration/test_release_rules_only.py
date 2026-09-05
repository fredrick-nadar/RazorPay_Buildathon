"""Release check: ARGUS stays usable with every live external service absent.

The submission must not depend on a live model provider, a Telegram bot, or
Razorpay being reachable. This test removes all model credentials, disables
Telegram, and arms tripwires on every real outbound boundary in the backend,
then runs the deterministic synthetic dataset in rules-only mode and requires
measured output with no invented provider claim.

The tripwires are proven non-vacuous: each one is called directly and must
raise. A test that merely "made no call" because it patched the wrong symbol
would pass silently otherwise.
"""

from __future__ import annotations

import socket
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.ai.base as ai_base
import app.importers.razorpay_client as razorpay_client
import app.telegram.channel as telegram_channel
import app.voice.transcribe as voice_transcribe
from app.config import Settings
from app.domain.enums import BatchStatus
from app.main import create_app
from app.persistence.database import open_database
from app.runs import execute_run

REPO_ROOT = Path(__file__).resolve().parents[3]
DEV_INPUTS = REPO_ROOT / "datasets" / "dev" / "inputs"

MODEL_ENV_NAMES = (
    "ARGUS_GROQ_API_KEY",
    "GROQ_API_KEY",
    "ARGUS_GEMINI_API_KEY",
    "ARGUS_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "ARGUS_SARVAM_API_KEY",
    "SARVAM_API_KEY",
    "ARGUS_MODEL_API_KEY",
    "MODEL_API_KEY",
    "LLM_API_KEY",
    "ARGUS_LLM_API_KEY",
    "ARGUS_RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_ID",
    "ARGUS_RAZORPAY_KEY_SECRET",
    "RAZORPAY_KEY_SECRET",
    "ARGUS_TELEGRAM_BOT_TOKEN",
)


class OutboundNetworkAttempted(AssertionError):
    """Raised by a tripwire when release code reaches for the network."""


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", ""})


def _is_loopback(address: Any) -> bool:
    """True only for an address that cannot leave this machine."""
    if isinstance(address, tuple) and address:
        host = str(address[0])
        return host in _LOOPBACK_HOSTS or host.startswith("127.")
    return False


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Strip live credentials and arm every real outbound boundary to fail."""
    for name in MODEL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    calls: dict[str, Any] = {"attempts": []}

    def tripwire(label: str) -> Any:
        def _fail(*args: object, **kwargs: object) -> None:
            calls["attempts"].append(label)
            raise OutboundNetworkAttempted(f"outbound call attempted via {label}")

        return _fail

    # Socket connection is the ultimate boundary: nothing leaves the host
    # without it. Loopback stays permitted because the ASGI test transport and
    # asyncio's own self-pipe use it; only a call that would leave the machine
    # trips the wire.
    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection

    def guarded_connect(sock: socket.socket, address: Any, *args: object) -> Any:
        if not _is_loopback(address):
            calls["attempts"].append("socket.connect")
            raise OutboundNetworkAttempted(f"outbound socket connect to {address!r}")
        return real_connect(sock, address, *args)

    def guarded_create_connection(address: Any, *args: object, **kwargs: object) -> Any:
        if not _is_loopback(address):
            calls["attempts"].append("socket.create_connection")
            raise OutboundNetworkAttempted(f"outbound connection to {address!r}")
        return real_create_connection(address, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)

    # Transport symbols, patched exactly where they actually live.
    #
    # app.ai.base and app.importers.razorpay_client both call
    # ``urllib.request.urlopen`` through the imported stdlib module, which is a
    # single shared object. Patching it "twice" would only overwrite the same
    # attribute, so it is armed ONCE and labelled as the one shared boundary it
    # is; claiming two independent tripwires there would be false.
    #
    # app.telegram.channel and app.voice.transcribe do ``from urllib.request
    # import urlopen``, so each holds its own module-local name that the shared
    # patch above would not affect. Those are genuinely separate and are armed
    # separately. test_tripwire_topology_matches_the_import_graph pins these
    # facts so the labels can never drift from the code.
    monkeypatch.setattr(urllib.request, "urlopen", tripwire("urllib.request.urlopen"))
    monkeypatch.setattr(telegram_channel, "urlopen", tripwire("telegram.urlopen"))
    monkeypatch.setattr(voice_transcribe, "urlopen", tripwire("voice.urlopen"))
    calls["armed"] = (
        "socket.connect",
        "socket.create_connection",
        "urllib.request.urlopen",
        "telegram.urlopen",
        "voice.urlopen",
    )
    yield calls


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "rules-only.sqlite3",
        import_staging_root=tmp_path / "staging",
        ai_provider="none",
        telegram_enabled=False,
        _env_file=None,
    )


def test_tripwire_topology_matches_the_import_graph() -> None:
    """The tripwire labels must describe the real import graph, not a guess."""
    # One shared stdlib module object, so one shared boundary.
    assert ai_base.urllib.request is razorpay_client.urllib.request is urllib.request
    assert "urlopen" not in vars(ai_base)
    assert "urlopen" not in vars(razorpay_client)
    # Genuinely separate module-local names.
    assert "urlopen" in vars(telegram_channel)
    assert "urlopen" in vars(voice_transcribe)


def test_tripwires_are_not_vacuous(offline: dict[str, Any]) -> None:
    """Every armed boundary must genuinely fail when it is called."""
    assert offline["armed"] == (
        "socket.connect",
        "socket.create_connection",
        "urllib.request.urlopen",
        "telegram.urlopen",
        "voice.urlopen",
    )
    # The shared stdlib boundary, reached through each of its two callers.
    with pytest.raises(OutboundNetworkAttempted):
        ai_base.urllib.request.urlopen("https://api.groq.com/openai/v1/models")
    with pytest.raises(OutboundNetworkAttempted):
        razorpay_client.urllib.request.urlopen("https://api.razorpay.com/v1/payments")
    # The two module-local bindings.
    with pytest.raises(OutboundNetworkAttempted):
        telegram_channel.urlopen("https://api.telegram.org/botX/getMe")
    with pytest.raises(OutboundNetworkAttempted):
        voice_transcribe.urlopen("https://api.sarvam.ai/speech-to-text")
    # The ultimate boundary.
    with pytest.raises(OutboundNetworkAttempted):
        socket.create_connection(("example.com", 443))

    assert offline["attempts"] == [
        "urllib.request.urlopen",
        "urllib.request.urlopen",
        "telegram.urlopen",
        "voice.urlopen",
        "socket.create_connection",
    ]


def test_rules_only_run_produces_measured_output_with_no_live_service(
    offline: dict[str, Any], tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    assert settings.rules_only is True, "no live model credential may remain configured"

    database = open_database(settings)
    try:
        result = execute_run(DEV_INPUTS, database)
    finally:
        database.close()

    assert result.status is BatchStatus.COMPLETED
    assert result.economic_output_hash, "a measured economic output hash must exist"

    summary = result.summary
    # Measured, not asserted: the counts come from the run, and the provider is
    # honestly reported as absent rather than as a model that never ran.
    assert int(summary["eligible_record_count"]) > 0
    assert int(summary["matched_record_count"]) > 0
    assert summary["mode"] == "rules-only"
    assert summary["provider_id"] == "none"
    assert summary["investigation_status"] == "NOT_INVESTIGATED"

    assert offline["attempts"] == [], "a rules-only run must make no outbound call"


def test_application_starts_and_reports_health_with_every_provider_absent(
    offline: dict[str, Any], tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/v1/health")
        version = client.get("/api/v1/version")
    assert health.status_code == 200
    assert version.status_code == 200
    assert offline["attempts"] == [], "startup must not contact any external service"


def test_telegram_disabled_makes_no_network_call_and_never_blocks_startup(
    offline: dict[str, Any], tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    assert settings.telegram_enabled is False
    with TestClient(create_app(settings)) as client:
        channel = client.app.state.telegram_channel  # type: ignore[attr-defined]
        status = channel.status()
        assert status["enabled"] is False
        assert status["state"] == "DISABLED"
        # Reconciliation stays available while the channel is off.
        assert client.get("/api/v1/health").status_code == 200
    assert offline["attempts"] == []


def test_telegram_failure_degrades_only_the_telegram_channel(tmp_path: Path) -> None:
    """An unreachable Telegram API must not take reconciliation down with it."""
    from app.telegram.channel import TelegramApiError, TelegramChannel
    from app.workflow.controller import ReconciliationController

    settings = Settings(
        db_path=tmp_path / "tg.sqlite3",
        import_staging_root=tmp_path / "staging",
        telegram_enabled=True,
        telegram_bot_token="synthetic-not-a-real-token",  # noqa: S106 - synthetic
        telegram_poll_timeout_s=1,
        _env_file=None,
    )
    database = open_database(settings)
    controller = ReconciliationController(database, settings)
    controller.start()

    class UnreachableClient:
        def get_me(self) -> dict[str, Any]:
            raise TelegramApiError("Telegram request failed.")

    channel = TelegramChannel(settings, database, controller, client=UnreachableClient())
    try:
        channel.start()
        channel.close()
        assert channel.status()["failure_code"] == "BOT_AUTHENTICATION_FAILED"
        # The financial path is untouched by the channel outage.
        result = execute_run(DEV_INPUTS, database)
        assert result.status is BatchStatus.COMPLETED
    finally:
        controller.close()
        database.close()


def test_telegram_config_problems_do_not_block_rules_only_startup(tmp_path: Path) -> None:
    # Disabled + a junk token is not a startup failure.
    settings = Settings(
        db_path=tmp_path / "junk.sqlite3",
        import_staging_root=tmp_path / "staging",
        telegram_enabled=False,
        telegram_bot_token="   ",
        _env_file=None,
    )
    assert settings.telegram_enabled is False
    assert settings.telegram_bot_token is None
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/health").status_code == 200
