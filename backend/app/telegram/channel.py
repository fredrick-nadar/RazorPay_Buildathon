"""Outbound-polling Telegram adapter over existing ARGUS intake services."""

from __future__ import annotations

import hashlib
import json
import secrets
import string
import threading
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from app.ai.base import USER_AGENT
from app.ai.selection import InvestigatorUnavailableError
from app.config import Settings
from app.importers.csv_intake import commit_csv_evidence
from app.importers.intake_workflow import get_session_status, start_session_job
from app.importers.schema_mapping import MAX_CSV_BYTES, DocumentType, analyze_csv
from app.importers.session_staging import SourceRevisionError
from app.persistence.database import Database
from app.workflow.controller import ReconciliationController

PAIRING_ALPHABET = string.ascii_uppercase.replace("I", "").replace("O", "") + "23456789"
PAIRING_CODE_LENGTH = 10
TELEGRAM_API_ROOT = "https://api.telegram.org"


class TelegramApiError(RuntimeError):
    """A sanitized Telegram transport or response failure."""


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("ascii", errors="ignore")).hexdigest()


class TelegramClient:
    """Small stdlib client for the Bot API methods ARGUS actually uses."""

    def __init__(self, token: str) -> None:
        self._token = token

    def _json(self, method: str, payload: dict[str, Any], timeout_s: float) -> Any:
        request = Request(
            f"{TELEGRAM_API_ROOT}/bot{self._token}/{method}",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed HTTPS host
                body = response.read(2 * 1024 * 1024)
            parsed = json.loads(body)
            if not isinstance(parsed, dict) or parsed.get("ok") is not True:
                raise TelegramApiError("Telegram rejected the request.")
            return parsed.get("result")
        except TelegramApiError:
            raise
        except (HTTPError, URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError):
            raise TelegramApiError("Telegram request failed.") from None

    def get_me(self) -> dict[str, Any]:
        result = self._json("getMe", {}, 10)
        if not isinstance(result, dict) or not isinstance(result.get("id"), int):
            raise TelegramApiError("Telegram bot identity was invalid.")
        return result

    def delete_webhook(self) -> None:
        self._json("deleteWebhook", {"drop_pending_updates": False}, 10)

    def get_updates(self, offset: int, timeout_s: int) -> list[dict[str, Any]]:
        result = self._json(
            "getUpdates",
            {"offset": offset, "timeout": timeout_s, "allowed_updates": ["message"]},
            timeout_s + 5,
        )
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise TelegramApiError("Telegram updates were invalid.")
        return result

    def send_message(self, chat_id: int, text: str) -> None:
        self._json("sendMessage", {"chat_id": chat_id, "text": text}, 10)

    def download_document(self, file_id: str) -> bytes:
        result = self._json("getFile", {"file_id": file_id}, 10)
        file_path = result.get("file_path") if isinstance(result, dict) else None
        if not isinstance(file_path, str):
            raise TelegramApiError("Telegram file metadata was invalid.")
        path = PurePosixPath(file_path)
        if path.is_absolute() or ".." in path.parts or "\\" in file_path:
            raise TelegramApiError("Telegram file path was invalid.")
        request = Request(
            f"{TELEGRAM_API_ROOT}/file/bot{self._token}/{quote(file_path, safe='/')}",
            headers={"User-Agent": USER_AGENT},
        )
        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed HTTPS host
                content: bytes = response.read(MAX_CSV_BYTES + 1)
        except (HTTPError, URLError, OSError, TimeoutError):
            raise TelegramApiError("Telegram file download failed.") from None
        if len(content) > MAX_CSV_BYTES:
            raise TelegramApiError("Telegram file exceeds the ARGUS CSV limit.")
        return content


class TelegramChannel:
    """Durable pairing, commands and CSV intake for one configured Telegram bot."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        controller: ReconciliationController,
        *,
        client: TelegramClient | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.controller = controller
        token = settings.telegram_bot_token
        self.client = client or (TelegramClient(token.get_secret_value()) if token else None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = "DISABLED" if not settings.telegram_enabled else "STARTING"
        self._bot_id: int | None = None
        self._bot_username: str | None = None
        self._failure_code: str | None = None

    def start(self) -> None:
        if not self.settings.telegram_enabled or self.client is None or self._thread is not None:
            return
        # ponytail: one poller per process; use a dedicated worker before multi-process deploys.
        self._thread = threading.Thread(target=self._run, name="argus-telegram", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(self.settings.telegram_poll_timeout_s + 6)
        if self._state != "DISABLED":
            self._state = "STOPPED"

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.settings.telegram_bot_token is not None,
            "enabled": self.settings.telegram_enabled,
            "state": self._state,
            "bot_username": self._bot_username,
            "failure_code": self._failure_code,
            "delivery": "LONG_POLLING",
            "synthetic_csv_only": True,
        }

    def _run(self) -> None:
        assert self.client is not None
        try:
            identity = self.client.get_me()
            self.client.delete_webhook()
            self._bot_id = int(identity["id"])
            username = identity.get("username")
            self._bot_username = str(username) if isinstance(username, str) else None
            offset = self._offset(self._bot_id)
            self._state = "RUNNING"
            self._failure_code = None
            while not self._stop.is_set():
                try:
                    updates = self.client.get_updates(offset, self.settings.telegram_poll_timeout_s)
                    for update in updates:
                        update_id = update.get("update_id")
                        if not isinstance(update_id, int) or update_id < offset:
                            continue
                        try:
                            self.process_update(self._bot_id, update)
                        except Exception:  # noqa: BLE001 - one malformed update cannot kill polling
                            self._state = "DEGRADED"
                            self._failure_code = "UPDATE_PROCESSING_FAILED"
                        offset = update_id + 1
                        self._save_offset(self._bot_id, offset)
                    self._state = "RUNNING"
                    self._failure_code = None
                except TelegramApiError:
                    self._state = "DEGRADED"
                    self._failure_code = "TELEGRAM_UNAVAILABLE"
                    self._stop.wait(2)
        except TelegramApiError:
            self._state = "DEGRADED"
            self._failure_code = "BOT_AUTHENTICATION_FAILED"

    def create_pairing(self, session_id: str) -> dict[str, Any]:
        if not self.settings.telegram_enabled:
            raise ValueError("Telegram is not enabled.")
        code = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))
        created = _now()
        expires = created + timedelta(seconds=self.settings.telegram_pairing_ttl_s)
        with self.database.transaction(immediate=True):
            self.database.execute(
                "UPDATE telegram_pairings SET status = 'REVOKED', revoked_at_utc = ? "
                "WHERE session_id = ? AND status = 'PENDING'",
                (_iso(created), session_id),
            )
            self.database.execute(
                "INSERT INTO telegram_pairings "
                "(pairing_id, code_hash, session_id, status, created_at_utc, expires_at_utc) "
                "VALUES (?, ?, ?, 'PENDING', ?, ?)",
                (f"tgp-{uuid4().hex}", _code_hash(code), session_id, _iso(created), _iso(expires)),
            )
        return {"pairing_code": code, "session_id": session_id, "expires_at_utc": _iso(expires)}

    def session_connection(self, session_id: str) -> dict[str, Any]:
        row = self.database.query_one(
            "SELECT pairing_id, status, telegram_user_id, telegram_chat_id, chat_type, "
            "created_at_utc, expires_at_utc, claimed_at_utc FROM telegram_pairings "
            "WHERE session_id = ? ORDER BY created_at_utc DESC LIMIT 1",
            (session_id,),
        )
        if row is None:
            return {"session_id": session_id, "status": "NOT_PAIRED"}
        if str(row["status"]) == "PENDING" and str(row["expires_at_utc"]) <= _iso(_now()):
            self.database.execute(
                "UPDATE telegram_pairings SET status = 'REVOKED', revoked_at_utc = ? "
                "WHERE pairing_id = ? AND status = 'PENDING'",
                (_iso(_now()), str(row["pairing_id"])),
            )
            return {"session_id": session_id, "status": "REVOKED"}
        return {
            "session_id": session_id,
            "connection_id": str(row["pairing_id"]),
            "status": str(row["status"]),
            "telegram_user_id": row["telegram_user_id"],
            "telegram_chat_id": row["telegram_chat_id"],
            "chat_type": row["chat_type"],
            "created_at_utc": str(row["created_at_utc"]),
            "expires_at_utc": str(row["expires_at_utc"]),
            "claimed_at_utc": row["claimed_at_utc"],
        }

    def revoke_session(self, session_id: str) -> bool:
        now = _iso(_now())
        row = self.database.query_one(
            "SELECT pairing_id FROM telegram_pairings WHERE session_id = ? "
            "AND status IN ('PENDING', 'CLAIMED') LIMIT 1",
            (session_id,),
        )
        if row is None:
            return False
        self.database.execute(
            "UPDATE telegram_pairings SET status = 'REVOKED', pending_upload_type = NULL, "
            "revoked_at_utc = ? WHERE session_id = ? AND status IN ('PENDING', 'CLAIMED')",
            (now, session_id),
        )
        return True

    def process_update(self, bot_id: int, update: dict[str, Any]) -> None:
        """Process one Telegram update; public for deterministic offline tests."""
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return
        chat_id, user_id = chat.get("id"), sender.get("id")
        chat_type = chat.get("type")
        if not isinstance(chat_id, int) or not isinstance(user_id, int):
            return
        if sender.get("is_bot") is True:
            return
        if chat_type != "private":
            self._reply(chat_id, "ARGUS accepts financial evidence only in a private bot chat.")
            return

        text = message.get("text") or message.get("caption") or ""
        tokens = str(text).strip().split()
        command, arguments = (tokens[0], tokens[1:]) if tokens else ("", [])
        command = command.split("@", 1)[0].lower() if command.startswith("/") else ""
        document = message.get("document")

        if command in ("/start", "/help"):
            self._reply(chat_id, self._help())
            return
        if command == "/pair":
            if len(arguments) != 1 or not self._claim(
                arguments[0], bot_id, user_id, chat_id, str(chat_type)
            ):
                self._reply(chat_id, "Pairing failed or expired. Generate a new code in ARGUS.")
            else:
                self._reply(chat_id, "Paired. Use /upload bank or /upload ledger.")
            return
        if command in ("/approve", "/apply", "/resolve", "/razorpay"):
            self._reply(
                chat_id,
                "This command is refused. Credentials and financial approvals stay in ARGUS.",
            )
            return

        pairing = self._active_pairing(bot_id, user_id, chat_id)
        if pairing is None:
            self._reply(chat_id, "Pair this chat from the ARGUS Import Data screen first.")
            return
        if command == "/status":
            self._reply(chat_id, self._status_text(str(pairing["session_id"])))
            return
        if command == "/cases":
            self._reply(chat_id, self._cases_text(str(pairing["session_id"])))
            return
        if command == "/reconcile":
            self._start_reconciliation(chat_id, str(pairing["session_id"]))
            return
        if command == "/upload":
            requested = arguments[0].lower() if arguments else ""
            source: DocumentType | None = {
                "bank": "bank_entries",
                "ledger": "ledger_entries",
            }.get(requested)  # type: ignore[assignment]
            if source is None:
                self._reply(chat_id, "Use /upload bank or /upload ledger.")
                return
            self._set_pending(str(pairing["pairing_id"]), source)
            if isinstance(document, dict):
                self._receive_document(chat_id, pairing, source, document)
            else:
                self._reply(chat_id, f"Send the {requested} CSV in your next message.")
            return
        if isinstance(document, dict):
            pending = pairing["pending_upload_type"]
            if pending in ("bank_entries", "ledger_entries"):
                self._receive_document(chat_id, pairing, pending, document)
            else:
                self._reply(
                    chat_id, "Choose the evidence type first: /upload bank or /upload ledger."
                )
            return
        self._reply(chat_id, self._help())

    def _claim(self, code: str, bot_id: int, user_id: int, chat_id: int, chat_type: str) -> bool:
        now = _iso(_now())
        with self.database.transaction(immediate=True):
            row = self.database.query_one(
                "SELECT pairing_id, session_id FROM telegram_pairings WHERE code_hash = ? "
                "AND status = 'PENDING' AND expires_at_utc > ?",
                (_code_hash(code), now),
            )
            if row is None:
                return False
            self.database.execute(
                "UPDATE telegram_pairings SET status = 'REVOKED', revoked_at_utc = ?, "
                "pending_upload_type = NULL WHERE status = 'CLAIMED' AND bot_id = ? "
                "AND (session_id = ? OR (telegram_user_id = ? AND telegram_chat_id = ?))",
                (now, bot_id, str(row["session_id"]), user_id, chat_id),
            )
            self.database.execute(
                "UPDATE telegram_pairings SET status = 'CLAIMED', bot_id = ?, "
                "telegram_user_id = ?, telegram_chat_id = ?, chat_type = ?, claimed_at_utc = ? "
                "WHERE pairing_id = ?",
                (bot_id, user_id, chat_id, chat_type, now, str(row["pairing_id"])),
            )
        return True

    def _active_pairing(self, bot_id: int, user_id: int, chat_id: int) -> Any:
        return self.database.query_one(
            "SELECT * FROM telegram_pairings WHERE status = 'CLAIMED' AND bot_id = ? "
            "AND telegram_user_id = ? AND telegram_chat_id = ?",
            (bot_id, user_id, chat_id),
        )

    def _set_pending(self, pairing_id: str, source: DocumentType | None) -> None:
        self.database.execute(
            "UPDATE telegram_pairings SET pending_upload_type = ? WHERE pairing_id = ? "
            "AND status = 'CLAIMED'",
            (source, pairing_id),
        )

    def _receive_document(
        self, chat_id: int, pairing: Any, source: DocumentType, document: dict[str, Any]
    ) -> None:
        filename, file_id, file_size = (
            document.get("file_name"),
            document.get("file_id"),
            document.get("file_size"),
        )
        if (
            not isinstance(filename, str)
            or not filename.lower().endswith(".csv")
            or not isinstance(file_id, str)
            or (isinstance(file_size, int) and file_size > MAX_CSV_BYTES)
        ):
            self._reply(chat_id, "Upload refused. Send one CSV no larger than 5 MB.")
            return
        try:
            assert self.client is not None
            raw = self.client.download_document(file_id)
            content = raw.decode("utf-8-sig")
            if "\x00" in content:
                raise ValueError("binary content")
            analysis = analyze_csv(content=content, document_type=source)
            if analysis["status"] != "READY":
                self._set_pending(str(pairing["pairing_id"]), None)
                self._reply(
                    chat_id,
                    "Nothing was imported. This CSV needs mapping review in the ARGUS dashboard.",
                )
                return
            mapping = {
                str(item["target_field"]): str(item["source_column"])
                for item in analysis["mappings"]
            }
            result = commit_csv_evidence(
                settings=self.settings,
                database=self.database,
                filename=self._safe_filename(filename),
                content=content,
                file_type=source,
                session_id=str(pairing["session_id"]),
                mapping=mapping,
                origin="TELEGRAM_CSV",
            )
            self._set_pending(str(pairing["pairing_id"]), None)
            self._reply(
                chat_id,
                f"Imported {result['accepted_count']} rows; "
                f"{result['quarantined_count']} quarantined. Use /status.",
            )
        except (TelegramApiError, UnicodeDecodeError, ValueError, SourceRevisionError):
            self._reply(chat_id, "Import failed safely. No active evidence was replaced.")

    def _start_reconciliation(self, chat_id: int, session_id: str) -> None:
        try:
            job = start_session_job(
                settings=self.settings,
                database=self.database,
                controller=self.controller,
                session_id=session_id,
                mode="agent",
            )
        except (FileNotFoundError, InvestigatorUnavailableError, SourceRevisionError, ValueError):
            self._reply(chat_id, "Reconciliation could not start. Check /status in ARGUS.")
            return
        if job["status"] == "BLOCKED":
            self._reply(chat_id, "Reconciliation is waiting for required evidence. Use /status.")
        else:
            self._reply(chat_id, f"Reconciliation job {job['job_id']} is {job['status']}.")

    def _status_text(self, session_id: str) -> str:
        try:
            status = get_session_status(self.settings, self.database, session_id)
        except SourceRevisionError:
            return "ARGUS could not verify the active evidence manifest. Open the dashboard."
        ready = status["ready_source_groups"]
        state = status["lifecycle_state"]
        row = self.database.query_one(
            "SELECT job_id, status FROM reconciliation_jobs WHERE session_id = ? "
            "ORDER BY created_at_utc DESC LIMIT 1",
            (session_id,),
        )
        job = f" Latest job: {row['job_id']} ({row['status']})." if row else ""
        return f"ARGUS intake: {ready}/3 source groups ready. State: {state}.{job}"

    def _cases_text(self, session_id: str) -> str:
        row = self.database.query_one(
            "SELECT run_id FROM reconciliation_jobs WHERE session_id = ? AND run_id IS NOT NULL "
            "ORDER BY created_at_utc DESC LIMIT 1",
            (session_id,),
        )
        if row is None:
            return "No completed reconciliation run exists for this session."
        counts = self.database.query_all(
            "SELECT status, COUNT(*) AS count FROM cases WHERE run_id = ? GROUP BY status",
            (str(row["run_id"]),),
        )
        summary = ", ".join(f"{item['status']}: {item['count']}" for item in counts) or "none"
        return f"Run {row['run_id']} cases — {summary}. Review details and approvals in ARGUS."

    def _offset(self, bot_id: int) -> int:
        row = self.database.query_one(
            "SELECT next_update_id FROM telegram_bot_offsets WHERE bot_id = ?", (bot_id,)
        )
        return int(row["next_update_id"]) if row else 0

    def _save_offset(self, bot_id: int, offset: int) -> None:
        self.database.execute(
            "INSERT INTO telegram_bot_offsets (bot_id, next_update_id, updated_at_utc) "
            "VALUES (?, ?, ?) ON CONFLICT(bot_id) DO UPDATE SET "
            "next_update_id = excluded.next_update_id, updated_at_utc = excluded.updated_at_utc",
            (bot_id, offset, _iso(_now())),
        )

    def _reply(self, chat_id: int, text: str) -> None:
        try:
            if self.client is not None:
                self.client.send_message(chat_id, text[:4096])
        except TelegramApiError:
            self._state = "DEGRADED"
            self._failure_code = "TELEGRAM_SEND_FAILED"

    @staticmethod
    def _help() -> str:
        return (
            "ARGUS synthetic evidence channel\n"
            "/pair CODE — connect this private chat\n"
            "/upload bank — upload bank CSV\n"
            "/upload ledger — upload merchant-ledger CSV\n"
            "/status — intake and job status\n"
            "/reconcile — start the existing ARGUS controller\n"
            "/cases — latest exception counts\n"
            "Razorpay credentials and approvals are dashboard-only."
        )

    @staticmethod
    def _safe_filename(filename: str) -> str:
        leaf = PurePosixPath(filename.replace("\\", "/")).name
        safe = "".join(
            character if character.isalnum() or character in "._-" else "_" for character in leaf
        )
        return (safe[:251] + ".csv") if not safe.lower().endswith(".csv") else safe[:255]


__all__ = ["TelegramApiError", "TelegramChannel", "TelegramClient"]
