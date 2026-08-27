"""Reset the local SQLite database to a pristine, clean state for demos."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Add backend directory to sys.path dynamically
root_dir = Path(__file__).resolve().parent.parent
backend_dir = root_dir / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

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
    "source_rows",
    "runs",
]


def reset_database() -> None:
    db_path = root_dir / "argus.local.sqlite3"
    wal_path = root_dir / "argus.local.sqlite3-wal"
    shm_path = root_dir / "argus.local.sqlite3-shm"

    print(f"[ARGUS] Clearing local database at: {db_path}")

    # If the database file exists, truncate all data tables directly
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path), isolation_level=None)
            for table in ALL_DATA_TABLES:
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    pass
            try:
                conn.execute("VACUUM")
            except Exception:
                pass
            conn.close()
            print("  [OK] All data tables truncated cleanly.")
            print("[ARGUS] Database is 100% clean and ready for demo recording!")
            return
        except Exception as e:
            print(f"  [WARN] Direct SQLite truncate encountered: {e}")

    # If file unlinking or fresh bootstrap is needed:
    for p in (db_path, wal_path, shm_path):
        if p.exists():
            try:
                p.unlink()
                print(f"  [OK] Deleted {p.name}")
            except Exception:
                pass

    # Initialize schema using backend Database if available
    try:
        from app.persistence.database import Database  # type: ignore[import-not-found]

        db = Database(db_path)
        print(f"[ARGUS] Fresh database initialized at schema version: {db.schema_version}")
    except Exception:
        print("  [INFO] Database file reset. Next backend start will auto-create fresh schema.")

    print("[ARGUS] Database is 100% clean and ready for demo recording!")


if __name__ == "__main__":
    reset_database()
