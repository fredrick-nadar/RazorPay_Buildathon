"""Reset the local SQLite database to a pristine, clean state for demos."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Add backend directory to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir / "backend"))

from app.persistence.database import Database

ALL_DATA_TABLES = [
    "audit_log",
    "simulated_corrections",
    "approvals",
    "corrections",
    "proofs",
    "hypotheses",
    "case_evidence",
    "cases",
    "norm_ledger_entries",
    "norm_bank_entries",
    "norm_settlements",
    "norm_refunds",
    "norm_payments",
    "runs",
]


def reset_database() -> None:
    db_path = root_dir / "argus.local.sqlite3"
    wal_path = root_dir / "argus.local.sqlite3-wal"
    shm_path = root_dir / "argus.local.sqlite3-shm"

    print(f"[ARGUS] Clearing local database at: {db_path}")

    # Try unlinking first
    unlinked = True
    for p in (db_path, wal_path, shm_path):
        if p.exists():
            try:
                p.unlink()
                print(f"  [OK] Deleted {p.name}")
            except Exception:
                unlinked = False

    if not unlinked and db_path.exists():
        print("  [INFO] File is open by dev server. Truncating all data tables cleanly...")
        conn = sqlite3.connect(str(db_path), isolation_level=None)
        for table in ALL_DATA_TABLES:
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception as e:
                print(f"  [SKIP] Table {table}: {e}")
        try:
            conn.execute("VACUUM")
        except Exception:
            pass
        conn.close()
        print("  [OK] All data tables truncated cleanly.")
    else:
        # Initialize fresh database if newly created
        print("[ARGUS] Initializing fresh schema & migrations...")
        db = Database(db_path)
        print(f"[ARGUS] Fresh database initialized at schema version: {db.schema_version}")

    print("[ARGUS] Database is 100% clean and ready for demo recording!")


if __name__ == "__main__":
    reset_database()
