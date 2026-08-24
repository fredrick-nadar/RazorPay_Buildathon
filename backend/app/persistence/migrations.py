"""Transactional SQLite schema migrations.

Schema evolution is explicit and versioned. A fresh database is created at
the v1 baseline (``app_meta`` only) and then walked through the migration
chain, so migration DDL is exercised on every boot, not only on upgrades.

Failure contract (Phase 2 review correction): a failing migration rolls back
completely and raises :class:`PersistenceMigrationError`. Because the failure
happens before any Phase 2 table exists, no run row can be created or marked
FAILED at that point; only failures after a successful migration and run
creation may persist a FAILED run status. The schema version stored in
``app_meta`` is the single source of truth and is left untouched by a rolled
back migration.
"""

from __future__ import annotations

import sqlite3

BASELINE_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class PersistenceMigrationError(RuntimeError):
    """A schema migration failed and was fully rolled back."""


def _migration_1_to_2_statements() -> tuple[str, ...]:
    return (
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            tenant_id TEXT NOT NULL,
            inputs_path TEXT NOT NULL,
            inputs_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            economic_output_hash TEXT,
            rule_manifest_json TEXT NOT NULL,
            started_at_utc TEXT NOT NULL,
            finished_at_utc TEXT,
            summary_json TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE source_rows (
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            source_type TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            source_file TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            raw_payload_json TEXT NOT NULL,
            state TEXT NOT NULL,
            quarantine_reason TEXT,
            quarantine_detail TEXT,
            duplicate_of_row_number INTEGER,
            PRIMARY KEY (run_id, source_type, source_row_number)
        )
        """,
        """
        CREATE TABLE norm_payments (
            run_id TEXT NOT NULL,
            payment_id TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            order_id TEXT,
            status TEXT NOT NULL,
            currency TEXT NOT NULL,
            gross_amount_paise INTEGER NOT NULL,
            fee_paise INTEGER NOT NULL,
            tax_paise INTEGER NOT NULL,
            captured_at_utc TEXT NOT NULL,
            settlement_id TEXT,
            PRIMARY KEY (run_id, payment_id)
        )
        """,
        """
        CREATE TABLE norm_refunds (
            run_id TEXT NOT NULL,
            refund_id TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            payment_id TEXT NOT NULL,
            status TEXT NOT NULL,
            currency TEXT NOT NULL,
            refund_amount_paise INTEGER NOT NULL,
            created_at_utc TEXT NOT NULL,
            settlement_id TEXT,
            PRIMARY KEY (run_id, refund_id)
        )
        """,
        """
        CREATE TABLE norm_settlements (
            run_id TEXT NOT NULL,
            settlement_id TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            settled_at_utc TEXT NOT NULL,
            window_start_utc TEXT NOT NULL,
            window_end_utc TEXT NOT NULL,
            status TEXT NOT NULL,
            currency TEXT NOT NULL,
            gross_credit_paise INTEGER NOT NULL,
            fee_paise INTEGER NOT NULL,
            tax_paise INTEGER NOT NULL,
            adjustment_paise INTEGER NOT NULL,
            net_amount_paise INTEGER NOT NULL,
            utr TEXT,
            PRIMARY KEY (run_id, settlement_id)
        )
        """,
        """
        CREATE TABLE norm_bank_entries (
            run_id TEXT NOT NULL,
            bank_entry_id TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            posted_at_utc TEXT NOT NULL,
            value_date TEXT NOT NULL,
            currency TEXT NOT NULL,
            signed_amount_paise INTEGER NOT NULL,
            narration TEXT NOT NULL,
            utr TEXT,
            account_fingerprint TEXT NOT NULL,
            PRIMARY KEY (run_id, bank_entry_id)
        )
        """,
        """
        CREATE TABLE norm_ledger_entries (
            run_id TEXT NOT NULL,
            ledger_entry_id TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            account_code TEXT NOT NULL,
            accounting_date TEXT NOT NULL,
            currency TEXT NOT NULL,
            signed_amount_paise INTEGER NOT NULL,
            source_reference TEXT,
            source_type TEXT,
            description TEXT NOT NULL,
            entry_origin TEXT NOT NULL,
            PRIMARY KEY (run_id, ledger_entry_id)
        )
        """,
        """
        CREATE TABLE match_groups (
            match_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            status TEXT NOT NULL,
            amount_paise INTEGER NOT NULL,
            created_at_utc TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE match_members (
            match_id TEXT NOT NULL REFERENCES match_groups(match_id),
            record_type TEXT NOT NULL,
            record_id TEXT NOT NULL,
            role TEXT NOT NULL,
            signed_contribution_paise INTEGER NOT NULL,
            PRIMARY KEY (match_id, record_type, record_id)
        )
        """,
        """
        CREATE TABLE cases (
            case_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            category_candidate TEXT NOT NULL,
            status TEXT NOT NULL,
            variance_paise INTEGER NOT NULL,
            affected_amount_paise INTEGER NOT NULL,
            proposed_delta_paise INTEGER,
            currency TEXT NOT NULL,
            summary TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL,
            opened_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE case_evidence (
            case_id TEXT NOT NULL REFERENCES cases(case_id),
            record_type TEXT NOT NULL,
            record_id TEXT NOT NULL,
            note TEXT,
            PRIMARY KEY (case_id, record_type, record_id)
        )
        """,
    )


def _migration_2_to_3_statements() -> tuple[str, ...]:
    """Phase 3 verifier artifacts (PRD 6.9, 6.10, 6.11).

    ``hypotheses``, ``proofs``, and ``corrections`` are run outputs only: a
    ``corrections`` row is a DRAFT preview of a verified correction, never an
    applied one. Applied simulated entries (a later phase) would be new
    ledger rows, never edits here.
    """
    return (
        """
        CREATE TABLE hypotheses (
            hypothesis_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(case_id),
            category TEXT NOT NULL,
            claim TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            status TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE proofs (
            proof_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(case_id),
            hypothesis_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            category TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            supported_evidence_json TEXT NOT NULL,
            conflicting_evidence_json TEXT NOT NULL,
            equations_json TEXT NOT NULL,
            rejected_alternatives_json TEXT NOT NULL,
            verifier_status TEXT NOT NULL,
            verifier_rule_id TEXT NOT NULL,
            verifier_rule_version TEXT NOT NULL,
            recon_manifest_fingerprint TEXT NOT NULL,
            verifier_manifest_fingerprint TEXT NOT NULL,
            proposed_delta_paise INTEGER,
            dry_run_json TEXT,
            authority_decision TEXT NOT NULL,
            requires_approval INTEGER NOT NULL,
            uncertainty_json TEXT NOT NULL,
            competing_candidates_json TEXT NOT NULL,
            missing_discriminator TEXT,
            recommended_next_step TEXT,
            canonical_hash TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE corrections (
            correction_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(case_id),
            proof_id TEXT NOT NULL,
            status TEXT NOT NULL,
            proposed_entry_json TEXT,
            target_ledger_entry_id TEXT,
            account_code TEXT,
            proposed_delta_paise INTEGER NOT NULL,
            variance_before_paise INTEGER NOT NULL,
            variance_after_paise INTEGER NOT NULL,
            totals_before_json TEXT NOT NULL,
            totals_after_json TEXT NOT NULL,
            warnings_json TEXT NOT NULL,
            uncertainty_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_proofs_case ON proofs(case_id)",
        "CREATE INDEX idx_corrections_case ON corrections(case_id)",
    )


def _migration_3_to_4_statements() -> tuple[str, ...]:
    """Phase 5 approval, simulated correction, and audit tables (PRD 6.11, 6.12, 11, 16).

    ``simulated_corrections`` stores applied corrections as new linked ledger entries
    (never modifying raw imported rows). ``approvals`` stores human reviewer authorizations
    and rejections. ``audit_log`` is an append-only event trail.
    """
    return (
        """
        CREATE TABLE simulated_corrections (
            correction_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(case_id),
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            proof_id TEXT NOT NULL REFERENCES proofs(proof_id),
            approval_id TEXT NOT NULL REFERENCES approvals(approval_id),
            target_ledger_entry_id TEXT,
            account_code TEXT NOT NULL,
            delta_paise INTEGER NOT NULL,
            applied_at_utc TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE
        )
        """,
        """
        CREATE TABLE approvals (
            approval_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES cases(case_id),
            proof_id TEXT NOT NULL REFERENCES proofs(proof_id),
            reviewer_id TEXT NOT NULL,
            action TEXT NOT NULL,
            notes TEXT,
            approved_at_utc TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE audit_log (
            event_id TEXT PRIMARY KEY,
            case_id TEXT,
            run_id TEXT,
            timestamp_utc TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            digest TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_sim_corrections_case ON simulated_corrections(case_id)",
        "CREATE INDEX idx_sim_corrections_run ON simulated_corrections(run_id)",
        "CREATE INDEX idx_approvals_case ON approvals(case_id)",
        "CREATE INDEX idx_audit_case ON audit_log(case_id)",
        "CREATE INDEX idx_audit_run ON audit_log(run_id)",
    )


# The chain stores statement-function NAMES resolved at call time so that
# tests can monkeypatch a broken migration into any step.
_MIGRATION_CHAIN: tuple[tuple[int, int, str], ...] = (
    (1, 2, "_migration_1_to_2_statements"),
    (2, 3, "_migration_2_to_3_statements"),
    (3, 4, "_migration_3_to_4_statements"),
)


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Walk the migration chain to the latest version and return it.

    Each migration runs inside one explicit ``BEGIN IMMEDIATE`` transaction
    that also bumps ``app_meta.schema_version``; any failure rolls the whole
    migration back and raises :class:`PersistenceMigrationError` with the
    stored schema version unchanged.
    """
    version = _read_schema_version(conn)
    for from_version, to_version, function_name in _MIGRATION_CHAIN:
        if version < from_version:
            raise PersistenceMigrationError(f"cannot migrate from unknown schema version {version}")
        if version >= to_version:
            continue
        statements = globals()[function_name]()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "UPDATE app_meta SET value = ? WHERE key = 'schema_version'",
                (str(to_version),),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise PersistenceMigrationError(
                f"migration v{from_version}->v{to_version} failed and was rolled "
                f"back; schema version remains {version}: {exc}"
            ) from exc
        version = to_version
    return version


def _read_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        raise PersistenceMigrationError("app_meta.schema_version is missing")
    try:
        return int(str(row["value"]))
    except (TypeError, ValueError) as exc:
        raise PersistenceMigrationError(
            f"app_meta.schema_version is not an integer: {row['value']!r}"
        ) from exc


def latest_schema_version() -> int:
    return _MIGRATION_CHAIN[-1][1]
