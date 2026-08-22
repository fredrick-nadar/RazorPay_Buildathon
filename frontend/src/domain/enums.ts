/**
 * Frozen domain enums mirrored from the Python backend.
 *
 * The canonical serialization is `contracts/domain_enums.json`, generated only
 * by `scripts/generate_domain_contracts.py`. The unit test compares these
 * objects to that contract read-only; never edit one side without
 * regenerating the other.
 */

function frozen<T extends Record<string, string>>(values: T): Readonly<T> {
  return Object.freeze(values);
}

export const SourceType = frozen({
  PAYMENT: "PAYMENT",
  REFUND: "REFUND",
  SETTLEMENT: "SETTLEMENT",
  BANK_ENTRY: "BANK_ENTRY",
  LEDGER_ENTRY: "LEDGER_ENTRY",
} as const);
export type SourceType = (typeof SourceType)[keyof typeof SourceType];

export const ExceptionCategory = frozen({
  DUPLICATE_LEDGER_POSTING: "DUPLICATE_LEDGER_POSTING",
  MISSING_REFUND_POSTING: "MISSING_REFUND_POSTING",
  SETTLEMENT_TIMING_WINDOW_SHIFT: "SETTLEMENT_TIMING_WINDOW_SHIFT",
  AMBIGUOUS_EVIDENCE: "AMBIGUOUS_EVIDENCE",
} as const);
export type ExceptionCategory =
  (typeof ExceptionCategory)[keyof typeof ExceptionCategory];

export const CaseStatus = frozen({
  OPEN: "OPEN",
  INVESTIGATING: "INVESTIGATING",
  VERIFICATION_FAILED: "VERIFICATION_FAILED",
  VERIFIED_RESOLVED: "VERIFIED_RESOLVED",
  APPROVAL_REQUIRED: "APPROVAL_REQUIRED",
  SIMULATED_APPLIED: "SIMULATED_APPLIED",
  UNRESOLVED: "UNRESOLVED",
  INVESTIGATION_FAILED: "INVESTIGATION_FAILED",
} as const);
export type CaseStatus = (typeof CaseStatus)[keyof typeof CaseStatus];

export const BatchStatus = frozen({
  CREATED: "CREATED",
  VALIDATING: "VALIDATING",
  NORMALIZED: "NORMALIZED",
  RECONCILING: "RECONCILING",
  INVESTIGATING: "INVESTIGATING",
  REVIEW_READY: "REVIEW_READY",
  COMPLETED: "COMPLETED",
  FAILED: "FAILED",
} as const);
export type BatchStatus = (typeof BatchStatus)[keyof typeof BatchStatus];

export const ActorType = frozen({
  SYSTEM: "SYSTEM",
  USER: "USER",
  MODEL: "MODEL",
} as const);
export type ActorType = (typeof ActorType)[keyof typeof ActorType];

export const EntryOrigin = frozen({
  IMPORTED: "IMPORTED",
  SIMULATED_CORRECTION: "SIMULATED_CORRECTION",
} as const);
export type EntryOrigin = (typeof EntryOrigin)[keyof typeof EntryOrigin];

export const HypothesisStatus = frozen({
  PROPOSED: "PROPOSED",
  SUPPORTED: "SUPPORTED",
  REJECTED: "REJECTED",
  INCONCLUSIVE: "INCONCLUSIVE",
} as const);
export type HypothesisStatus =
  (typeof HypothesisStatus)[keyof typeof HypothesisStatus];

export const VerifierStatus = frozen({
  PASS: "PASS",
  FAIL: "FAIL",
  INCONCLUSIVE: "INCONCLUSIVE",
} as const);
export type VerifierStatus =
  (typeof VerifierStatus)[keyof typeof VerifierStatus];

export const ReasonCode = frozen({
  MISSING_EVIDENCE: "MISSING_EVIDENCE",
  UNKNOWN_EVIDENCE_ID: "UNKNOWN_EVIDENCE_ID",
  CURRENCY_MISMATCH: "CURRENCY_MISMATCH",
  AMOUNT_MISMATCH: "AMOUNT_MISMATCH",
  NON_UNIQUE_EVIDENCE: "NON_UNIQUE_EVIDENCE",
  REFERENCE_CONFLICT: "REFERENCE_CONFLICT",
  OUTSIDE_ALLOWED_WINDOW: "OUTSIDE_ALLOWED_WINDOW",
  RECORD_ALREADY_CONSUMED: "RECORD_ALREADY_CONSUMED",
  CONTROL_TOTAL_VIOLATION: "CONTROL_TOTAL_VIOLATION",
  UNSUPPORTED_CATEGORY: "UNSUPPORTED_CATEGORY",
  INVALID_PROPOSED_DELTA: "INVALID_PROPOSED_DELTA",
} as const);
export type ReasonCode = (typeof ReasonCode)[keyof typeof ReasonCode];

export const ApprovalDecision = frozen({
  PENDING: "PENDING",
  APPROVED: "APPROVED",
  REJECTED: "REJECTED",
} as const);
export type ApprovalDecision =
  (typeof ApprovalDecision)[keyof typeof ApprovalDecision];

export const CorrectionStatus = frozen({
  DRAFT: "DRAFT",
  APPROVED: "APPROVED",
  SIMULATED_APPLIED: "SIMULATED_APPLIED",
  REJECTED: "REJECTED",
} as const);
export type CorrectionStatus =
  (typeof CorrectionStatus)[keyof typeof CorrectionStatus];

export const EdgeConfidence = frozen({
  EXACT: "EXACT",
  RULE: "RULE",
  HYPOTHESIS: "HYPOTHESIS",
  REJECTED: "REJECTED",
} as const);
export type EdgeConfidence =
  (typeof EdgeConfidence)[keyof typeof EdgeConfidence];

export const NodeType = frozen({
  PAYMENT: "PAYMENT",
  REFUND: "REFUND",
  SETTLEMENT: "SETTLEMENT",
  BANK_ENTRY: "BANK_ENTRY",
  LEDGER_ENTRY: "LEDGER_ENTRY",
  FEE: "FEE",
  TAX: "TAX",
  CORRECTION_PROPOSAL: "CORRECTION_PROPOSAL",
} as const);
export type NodeType = (typeof NodeType)[keyof typeof NodeType];

export const Currency = frozen({
  INR: "INR",
} as const);
export type Currency = (typeof Currency)[keyof typeof Currency];

export type EnumObject = Readonly<Record<string, string>>;

export const ENUMS: Readonly<Record<string, EnumObject>> = Object.freeze({
  SourceType,
  ExceptionCategory,
  CaseStatus,
  BatchStatus,
  ActorType,
  EntryOrigin,
  HypothesisStatus,
  VerifierStatus,
  ReasonCode,
  ApprovalDecision,
  CorrectionStatus,
  EdgeConfidence,
  NodeType,
  Currency,
});
