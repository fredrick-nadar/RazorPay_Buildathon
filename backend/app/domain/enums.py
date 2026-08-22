"""Frozen domain enums shared across the product (PRD sections 4-9, 11, 13).

The canonical serialization of these enums is ``contracts/domain_enums.json``,
generated ONLY by ``scripts/generate_domain_contracts.py``. The Python and
TypeScript tests compare code against that contract read-only; no test ever
writes it. To change an enum: update this module and
``frontend/src/domain/enums.ts`` together, rerun the generator explicitly,
and keep both contract tests green.
"""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    """Imported record source types (PRD 8.1)."""

    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    SETTLEMENT = "SETTLEMENT"
    BANK_ENTRY = "BANK_ENTRY"
    LEDGER_ENTRY = "LEDGER_ENTRY"


class ExceptionCategory(StrEnum):
    """Frozen MVP exception taxonomy (PRD 4.2). Exactly these four classes."""

    DUPLICATE_LEDGER_POSTING = "DUPLICATE_LEDGER_POSTING"
    MISSING_REFUND_POSTING = "MISSING_REFUND_POSTING"
    SETTLEMENT_TIMING_WINDOW_SHIFT = "SETTLEMENT_TIMING_WINDOW_SHIFT"
    AMBIGUOUS_EVIDENCE = "AMBIGUOUS_EVIDENCE"


class CaseStatus(StrEnum):
    """Mandatory case outcomes and interim states (PRD 4.3, 7.2)."""

    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    VERIFIED_RESOLVED = "VERIFIED_RESOLVED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    SIMULATED_APPLIED = "SIMULATED_APPLIED"
    UNRESOLVED = "UNRESOLVED"
    INVESTIGATION_FAILED = "INVESTIGATION_FAILED"


class BatchStatus(StrEnum):
    """Batch run states (PRD 7.1). FAILED is reachable from any processing state."""

    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    NORMALIZED = "NORMALIZED"
    RECONCILING = "RECONCILING"
    INVESTIGATING = "INVESTIGATING"
    REVIEW_READY = "REVIEW_READY"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ActorType(StrEnum):
    """Audit actor types (PRD 6.12)."""

    SYSTEM = "SYSTEM"
    USER = "USER"
    MODEL = "MODEL"


class EntryOrigin(StrEnum):
    """Ledger entry origin (PRD 6.6)."""

    IMPORTED = "IMPORTED"
    SIMULATED_CORRECTION = "SIMULATED_CORRECTION"


class HypothesisStatus(StrEnum):
    """Hypothesis lifecycle (PRD 6.9)."""

    PROPOSED = "PROPOSED"
    SUPPORTED = "SUPPORTED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class VerifierStatus(StrEnum):
    """Deterministic verifier results (PRD 9.1)."""

    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class ReasonCode(StrEnum):
    """Stable verifier reason codes (PRD 9.7)."""

    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    UNKNOWN_EVIDENCE_ID = "UNKNOWN_EVIDENCE_ID"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    NON_UNIQUE_EVIDENCE = "NON_UNIQUE_EVIDENCE"
    REFERENCE_CONFLICT = "REFERENCE_CONFLICT"
    OUTSIDE_ALLOWED_WINDOW = "OUTSIDE_ALLOWED_WINDOW"
    RECORD_ALREADY_CONSUMED = "RECORD_ALREADY_CONSUMED"
    CONTROL_TOTAL_VIOLATION = "CONTROL_TOTAL_VIOLATION"
    UNSUPPORTED_CATEGORY = "UNSUPPORTED_CATEGORY"
    INVALID_PROPOSED_DELTA = "INVALID_PROPOSED_DELTA"


class ApprovalDecision(StrEnum):
    """Approval decision states (PRD 6.11)."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CorrectionStatus(StrEnum):
    """Correction lifecycle (PRD 6.11, 7.3)."""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SIMULATED_APPLIED = "SIMULATED_APPLIED"
    REJECTED = "REJECTED"


class EdgeConfidence(StrEnum):
    """Evidence graph edge confidence sources (PRD 11)."""

    EXACT = "EXACT"
    RULE = "RULE"
    HYPOTHESIS = "HYPOTHESIS"
    REJECTED = "REJECTED"


class QuarantineReason(StrEnum):
    """Row-level quarantine reasons during normalization (PRD 8.1).

    A quarantined row is never dropped: it stays stored, counted, and
    traceable to its physical source row.
    """

    UNSUPPORTED_CURRENCY = "UNSUPPORTED_CURRENCY"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    INVALID_DATE = "INVALID_DATE"
    INVALID_MONEY = "INVALID_MONEY"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    UNKNOWN_STATUS = "UNKNOWN_STATUS"
    INVALID_ROW_SHAPE = "INVALID_ROW_SHAPE"
    DUPLICATE_ID_CONFLICT = "DUPLICATE_ID_CONFLICT"


class RelationshipType(StrEnum):
    """Deterministic match relationship types (PRD 6.7, 8.2)."""

    REFUND_OF_PAYMENT = "REFUND_OF_PAYMENT"
    MEMBER_OF_SETTLEMENT = "MEMBER_OF_SETTLEMENT"
    ADJUSTS_SETTLEMENT = "ADJUSTS_SETTLEMENT"
    SETTLEMENT_BANK_CREDIT = "SETTLEMENT_BANK_CREDIT"
    LEDGER_SOURCE = "LEDGER_SOURCE"
    CASE_EVIDENCE = "CASE_EVIDENCE"


class NodeType(StrEnum):
    """Evidence graph node types (PRD 11)."""

    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    SETTLEMENT = "SETTLEMENT"
    BANK_ENTRY = "BANK_ENTRY"
    LEDGER_ENTRY = "LEDGER_ENTRY"
    FEE = "FEE"
    TAX = "TAX"
    CASE = "CASE"
    CORRECTION_PROPOSAL = "CORRECTION_PROPOSAL"


class Currency(StrEnum):
    """Currencies supported by the MVP. INR only."""

    INR = "INR"


ALL_ENUMS: dict[str, type[StrEnum]] = {
    "SourceType": SourceType,
    "ExceptionCategory": ExceptionCategory,
    "CaseStatus": CaseStatus,
    "BatchStatus": BatchStatus,
    "ActorType": ActorType,
    "EntryOrigin": EntryOrigin,
    "HypothesisStatus": HypothesisStatus,
    "VerifierStatus": VerifierStatus,
    "ReasonCode": ReasonCode,
    "QuarantineReason": QuarantineReason,
    "ApprovalDecision": ApprovalDecision,
    "CorrectionStatus": CorrectionStatus,
    "EdgeConfidence": EdgeConfidence,
    "RelationshipType": RelationshipType,
    "NodeType": NodeType,
    "Currency": Currency,
}
