"""SQLite persistence boundary.

Default local persistence is a single SQLite file managed by the stdlib
``sqlite3`` module behind a small repository-style interface. The connection
runs in explicit-transaction (autocommit) mode so schema migrations can own
their ``BEGIN IMMEDIATE``/``COMMIT``/``ROLLBACK`` lifecycle: a failed
migration rolls back completely and raises
:class:`app.persistence.migrations.PersistenceMigrationError` before any
Phase 2 table exists, so no run row can be created or marked FAILED by a
migration failure. Only failures after a successful migration and run
creation may persist a FAILED run status.

Fresh databases are created at the v1 baseline and then migrated, so the
migration DDL is exercised on every boot. ``schema_version`` reports the
version stored in ``app_meta`` (the single source of truth), not a constant.

The connection is created with ``check_same_thread=False`` because ASGI
lifespan and sync request handlers run on different threads; a lock
serializes access so only one thread uses the connection at a time.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from app.config import Settings
from app.persistence.migrations import (
    PersistenceMigrationError,
    apply_migrations,
)
from app.persistence.migrations import latest_schema_version as _latest

__all__ = [
    "Database",
    "PersistenceMigrationError",
    "Repository",
    "ensure_persistent_parents",
    "open_database",
]

SCHEMA_VERSION = _latest()


class Database:
    """Thin, explicit SQLite connection wrapper with migrations and health check."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        # Reentrant so a held transaction can nest locked execute() calls.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._schema_version = 0
        try:
            with self._lock:
                from app.persistence.migrations import BASELINE_SCHEMA_V1

                self._conn.executescript(BASELINE_SCHEMA_V1)
                self._conn.execute(
                    "INSERT OR IGNORE INTO app_meta(key, value) VALUES ('schema_version', '1')"
                )
                self._schema_version = apply_migrations(self._conn)
        except BaseException:
            self._conn.close()
            raise

    @property
    def schema_version(self) -> int:
        """Schema version actually stored in ``app_meta`` after migration."""
        return self._schema_version

    def healthcheck(self) -> bool:
        """True when a real read against the migrated schema succeeds."""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()
        except sqlite3.Error:
            return False
        return row is not None and row["value"] == str(self._schema_version)

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

    def query_all(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[object] = ()) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self._conn.execute(sql, params).fetchone()
        return row

    def execute(self, sql: str, params: Sequence[object] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    def execute_many(self, sql: str, rows: Sequence[Sequence[object]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[None]:
        """Run a block inside one explicit transaction.

        The connection is in autocommit mode, so every standalone execute()
        commits immediately; bulk persistence should wrap its writes here so
        thousands of rows cost one commit instead of one fsync each. Any
        exception rolls the whole block back.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield
            except BaseException:
                self._conn.rollback()
                raise
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class Repository(Protocol):
    """Marker protocol for domain repositories.

    All Phase 1+ repositories (records, matches, cases, proofs, audit) must be
    backed by a ``Database`` obtained from ``open_database`` so the storage
    engine stays swappable behind this boundary.
    """


def ensure_persistent_parents(settings: Settings) -> None:
    """Create ONLY the parent directories the persistence boundary needs.

    The database parent and the import staging root are the two directories a
    deployment must be able to write. Nothing else is created, and an existing
    tree is left untouched, so a mounted volume is never reshaped by a boot.
    """
    db_parent = settings.db_path.expanduser().parent
    if str(db_parent):
        db_parent.mkdir(parents=True, exist_ok=True)
    settings.import_staging_root.expanduser().mkdir(parents=True, exist_ok=True)


def open_database(settings: Settings) -> Database:
    """Open (create, migrate) the configured SQLite database."""
    ensure_persistent_parents(settings)
    return Database(settings.db_path)
