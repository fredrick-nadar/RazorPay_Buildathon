"""SQLite persistence boundary.

Default local persistence is a single SQLite file managed by the stdlib
``sqlite3`` module behind a small repository-style interface. Phase 0 provides
connection management plus an ``app_meta`` key-value store so the health
endpoint can verify a real database round-trip. Domain repositories arrive in
later phases and must flow through this module rather than opening
connections directly.

The connection is created with ``check_same_thread=False`` because ASGI
lifespan and sync request handlers run on different threads; a lock
serializes access so only one thread uses the connection at a time.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Protocol

from app.config import Settings

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    """Thin, explicit SQLite connection wrapper with schema init and health check."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO app_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    @property
    def schema_version(self) -> int:
        return SCHEMA_VERSION

    def healthcheck(self) -> bool:
        """True when a real read against the initialized schema succeeds."""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()
        except sqlite3.Error:
            return False
        return row is not None and row["value"] == str(SCHEMA_VERSION)

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO app_meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class Repository(Protocol):
    """Marker protocol for future domain repositories.

    All Phase 1+ repositories (records, matches, cases, proofs, audit) must be
    backed by a ``Database`` obtained from ``open_database`` so the storage
    engine stays swappable behind this boundary.
    """


def open_database(settings: Settings) -> Database:
    """Open (and initialize) the configured SQLite database."""
    return Database(settings.db_path)
