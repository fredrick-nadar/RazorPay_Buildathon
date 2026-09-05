"""Telegram channel tests use a fake client and make no network requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.importers.session_staging import resolve_session_dir, session_source_status
from app.main import create_app
from app.persistence.database import Database
from app.telegram.channel import TelegramChannel, TelegramClient
from app.workflow.controller import ReconciliationController

BANK_CSV = (
    "bank_entry_id,posted_at_utc,value_date,currency,signed_amount,narration,utr,"
    "account_fingerprint\n"
    "bnk_TG001,2026-03-03T04:23:47Z,2026-03-03,INR,97.64,RAZORPAY,UTR_TG001,"
    "FP-SYNTHETIC\n"
)
LEDGER_CSV = (
    "ledger_entry_id,account_code,accounting_date,currency,signed_amount,source_reference,"
    "source_type,description,entry_origin\n"
    "led_TG001,1100-BANK,2026-03-03,INR,97.64,stl_TG001,SETTLEMENT,"
    "Synthetic settlement,IMPORTED\n"
)


class FakeTelegramClient(TelegramClient):
    def __init__(self) -> None:
        super().__init__("test-token")
        self.sent: list[tuple[int, str]] = []
        self.documents: dict[str, bytes] = {}

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    def download_document(self, file_id: str) -> bytes:
        return self.documents[file_id]

    def delete_webhook(self) -> None:
        return None


def _settings(tmp_path: Path, **updates: Any) -> Settings:
    return Settings(
        db_path=tmp_path / "telegram.sqlite3",
        import_staging_root=tmp_path / "imports",
        telegram_enabled=True,
        telegram_bot_token=SecretStr("test-token"),
        ai_provider="fake",
        _env_file=None,
        **updates,
    )


def _message(
    update_id: int,
    text: str = "",
    *,
    user_id: int = 101,
    chat_id: int = 202,
    chat_type: str = "private",
    document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": update_id,
        "from": {"id": user_id, "is_bot": False},
        "chat": {"id": chat_id, "type": chat_type},
        "text": text,
    }
    if document is not None:
        message["document"] = document
    return {"update_id": update_id, "message": message}


@pytest.fixture
def channel(tmp_path: Path) -> Any:
    settings = _settings(tmp_path)
    db = Database(settings.db_path)
    controller = ReconciliationController(db, settings, background=False)
    fake = FakeTelegramClient()
    service = TelegramChannel(settings, db, controller, client=fake)
    try:
        yield service, fake, db, settings
    finally:
        controller.close()
        db.close()


def _pair(
    service: TelegramChannel, fake: FakeTelegramClient, session_id: str = "tg-session"
) -> None:
    code = service.create_pairing(session_id)["pairing_code"]
    service.process_update(42, _message(1, f"/pair {code}"))
    assert fake.sent[-1][1].startswith("Paired")


def test_configuration_is_opt_in_and_requires_a_token(tmp_path: Path) -> None:
    disabled = Settings(db_path=tmp_path / "disabled.sqlite3", _env_file=None)
    assert disabled.telegram_enabled is False
    assert disabled.safe_summary()["telegram_configured"] is False
    with pytest.raises(ValidationError):
        Settings(telegram_enabled=True, telegram_bot_token=None, _env_file=None)


def test_pairing_is_one_time_private_and_never_persists_the_code(channel: Any) -> None:
    service, fake, db, _settings_value = channel
    result = service.create_pairing("tg-session")
    code = result["pairing_code"]
    stored = db.query_one("SELECT * FROM telegram_pairings")
    assert stored is not None
    assert code not in str(dict(stored))

    service.process_update(42, _message(1, f"/pair {code}", chat_type="group"))
    assert "private" in fake.sent[-1][1]
    service.process_update(42, _message(2, f"/pair {code}"))
    assert service.session_connection("tg-session")["status"] == "CLAIMED"
    service.process_update(42, _message(3, f"/pair {code}", user_id=999, chat_id=999))
    assert "failed or expired" in fake.sent[-1][1]


def test_bank_and_ledger_documents_use_the_shared_immutable_intake(channel: Any) -> None:
    service, fake, db, settings = channel
    _pair(service, fake)
    fake.documents = {"bank-file": BANK_CSV.encode(), "ledger-file": LEDGER_CSV.encode()}

    service.process_update(42, _message(2, "/upload bank"))
    service.process_update(
        42,
        _message(
            3,
            document={"file_id": "bank-file", "file_name": "bank.csv", "file_size": 200},
        ),
    )
    service.process_update(
        42,
        _message(
            4,
            "/upload ledger",
            document={"file_id": "ledger-file", "file_name": "ledger.csv", "file_size": 250},
        ),
    )

    status = session_source_status(resolve_session_dir(settings, "tg-session", create=False))
    assert set(status["active_sources"]) == {"bank_entries", "ledger_entries"}
    assert {row["origin"] for row in status["active_sources"].values()} == {"TELEGRAM_CSV"}
    assert "Imported 1 rows; 0 quarantined" in fake.sent[-1][1]

    service.process_update(42, _message(5, "/status"))
    assert "2/3 source groups ready" in fake.sent[-1][1]
    service.process_update(42, _message(6, "/reconcile"))
    assert "waiting for required evidence" in fake.sent[-1][1]
    service.process_update(42, _message(7, "/reconcile"))
    assert len(db.query_all("SELECT job_id FROM reconciliation_jobs")) == 1


def test_alias_mapping_is_refused_for_dashboard_review_without_activation(channel: Any) -> None:
    service, fake, _db, settings = channel
    _pair(service, fake)
    fake.documents["alias-file"] = (
        b"Txn ID,Transaction Date,Value Date,Currency,Amount,Description,UTR,Account Hash\n"
        b"bnk_1,2026-03-03T04:23:47Z,2026-03-03,INR,97.64,Synthetic,UTR_1,FP_1\n"
    )
    service.process_update(42, _message(2, "/upload bank"))
    service.process_update(
        42,
        _message(
            3,
            document={"file_id": "alias-file", "file_name": "alias.csv", "file_size": 200},
        ),
    )
    session = resolve_session_dir(settings, "tg-session", create=False)
    assert not session.is_dir()
    assert "mapping review" in fake.sent[-1][1]


def test_unauthorized_oversized_and_authority_commands_fail_closed(channel: Any) -> None:
    service, fake, _db, _settings_value = channel
    service.process_update(42, _message(1, "/upload bank"))
    assert "Pair this chat" in fake.sent[-1][1]
    _pair(service, fake)
    service.process_update(42, _message(2, "/approve case-anything"))
    assert "refused" in fake.sent[-1][1]
    service.process_update(42, _message(3, "/upload bank"))
    service.process_update(
        42,
        _message(
            4,
            document={
                "file_id": "large",
                "file_name": "large.csv",
                "file_size": 5 * 1024 * 1024 + 1,
            },
        ),
    )
    assert "Upload refused" in fake.sent[-1][1]


def test_pairing_control_plane_exposes_no_bot_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(TelegramChannel, "start", lambda self: None)
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        status = client.get("/api/v1/telegram/status")
        assert status.status_code == 200
        assert status.json()["configured"] is True
        created = client.post("/api/v1/telegram/pairing-codes", json={"session_id": "api-session"})
        assert created.status_code == 201
        body = created.json()
        assert "test-token" not in str(body)
        assert client.get("/api/v1/telegram/sessions/api-session").json()["status"] == "PENDING"
        assert client.delete("/api/v1/telegram/sessions/api-session").json()["revoked"] is True


def test_pairing_control_plane_refuses_non_local_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(TelegramChannel, "start", lambda self: None)
    with TestClient(create_app(settings), client=("198.51.100.9", 50000)) as client:
        assert client.get("/api/v1/telegram/status").status_code == 403
        assert (
            client.post(
                "/api/v1/telegram/pairing-codes", json={"session_id": "api-session"}
            ).status_code
            == 403
        )


def test_new_session_pairing_revokes_the_old_session_for_the_same_chat(channel: Any) -> None:
    service, fake, _db, _settings_value = channel
    _pair(service, fake, "first-session")
    _pair(service, fake, "second-session")
    assert service.session_connection("first-session")["status"] == "REVOKED"
    assert service.session_connection("second-session")["status"] == "CLAIMED"
