/**
 * Shared API response shapes for the control room.
 *
 * These mirror the backend route payloads (backend/app/api/routes_*.py).
 * The UI renders API results only; it contains no financial truth logic.
 */

/**
 * One cited evidence record, resolved to its immutable source row.
 *
 * `case_evidence` stores only a type, an id and a note. The backend now walks
 * the normalized row's `source_row_number` / `content_hash` pointer so a trace
 * can cite the actual source revision instead of a bare identifier.
 * `resolution` is RESOLVED, PARTIAL or UNRESOLVED: a citation whose record is
 * missing is reported honestly, never dropped and never given a placeholder.
 */
export interface EvidenceItem {
  record_type: string;
  record_id: string;
  note: string | null;
  resolution: "RESOLVED" | "PARTIAL" | "UNRESOLVED";
  resolution_reason: string | null;
  run_id: string | null;
  amount_paise: number | null;
  content_hash: string | null;
  source_row_number: number | null;
  source_type: string | null;
  source_file: string | null;
  source_record_id: string | null;
  source_state: string | null;
  source_content_hash: string | null;
  revision_matches_source: boolean | null;
  source_revision_id: string | null;
  source_origin: string | null;
  external_import_id: string | null;
}

/** Evidence as it appears on a case LIST row: identity only, no provenance. */
export interface EvidenceCitation {
  record_type: string;
  record_id: string;
  note: string | null;
}

export interface CaseSummary {
  case_id: string;
  run_id: string;
  category: string;
  status: string;
  variance_paise: number;
  affected_amount_paise: number;
  proposed_delta_paise: number | null;
  currency: string;
  summary: string;
  reason_codes: string[];
  evidence: EvidenceCitation[];
  opened_at_utc: string;
  updated_at_utc: string;
}

export interface HypothesisView {
  hypothesis_id: string;
  category: string;
  claim: string;
  evidence: string[];
  status: string;
  reason_codes: string[];
  created_at_utc: string;
}

export interface ProofView {
  proof_id: string;
  hypothesis_id: string;
  claim: string;
  category: string;
  evidence: string[];
  supported_evidence: string[];
  conflicting_evidence: string[];
  equations: Array<Record<string, unknown>>;
  rejected_alternatives: Array<Record<string, unknown>>;
  verifier_status: string;
  verifier_rule_id: string;
  verifier_rule_version: string;
  proposed_delta_paise: number | null;
  authority_decision: string;
  requires_approval: boolean;
  uncertainty: string[];
  competing_candidates: Array<Record<string, unknown>>;
  canonical_hash: string;
  created_at_utc: string;
}

export interface DryRunView {
  correction_id: string;
  proof_id: string;
  status: string;
  proposed_entry: Record<string, unknown> | null;
  target_ledger_entry_id: string | null;
  account_code: string | null;
  proposed_delta_paise: number;
  variance_before_paise: number;
  variance_after_paise: number;
  totals_before_paise: Record<string, number>;
  totals_after_paise: Record<string, number>;
  warnings: string[];
  uncertainty: string[];
  created_at_utc: string;
}

export interface SimulatedCorrectionView {
  correction_id: string;
  case_id: string;
  run_id: string;
  proof_id: string;
  approval_id: string;
  target_ledger_entry_id: string | null;
  account_code: string;
  delta_paise: number;
  applied_at_utc: string;
  idempotency_key: string;
}

export interface ApprovalView {
  approval_id: string;
  proof_id: string;
  reviewer_id: string;
  action: string;
  notes: string | null;
  approved_at_utc: string;
}

/** A case dossier carries provenance-resolved evidence, not bare citations. */
export interface CaseRecord extends Omit<CaseSummary, "evidence"> {
  evidence: EvidenceItem[];
}

export interface CaseDetail {
  case: CaseRecord;
  hypotheses: HypothesisView[];
  proof: ProofView | null;
  dry_run: DryRunView | null;
  simulated_correction: SimulatedCorrectionView | null;
  approvals: ApprovalView[];
}

export interface AuditLogItem {
  event_id: string;
  case_id: string | null;
  run_id: string | null;
  timestamp_utc: string;
  actor: string;
  action: string;
  payload: Record<string, unknown>;
  digest: string;
  /**
   * Position in the append-only log, from storage order. Wall-clock stamps can
   * tie; this is the authoritative order a view may render and assert on.
   */
  sequence: number;
}

export interface RunListItem {
  run_id: string;
  tenant_id: string;
  inputs_path: string;
  status: string;
  started_at_utc: string;
  finished_at_utc: string | null;
  economic_output_hash: string | null;
  summary: Record<string, unknown>;
}

export interface ReconcileResponse {
  run_id: string;
  status: string;
  reused: boolean;
  idempotency_key: string;
  economic_output_hash: string;
  summary: Record<string, unknown>;
}

/* ------------------------------------------------------------------ */
/* Master matrix                                                       */
/* ------------------------------------------------------------------ */

export type MatrixRecordType =
  | "PAYMENT"
  | "REFUND"
  | "SETTLEMENT"
  | "BANK_ENTRY"
  | "LEDGER_ENTRY";

export type MatrixLinkState = "RECONCILED" | "UNMATCHED";

/**
 * One normalized record in the run inventory.
 *
 * The matrix reports all five sources, each row exactly once, with its own
 * link state. Type-specific counterparty fields are optional because a bank
 * entry and a payment do not carry the same columns.
 */
export interface MatrixRecord {
  record_type: MatrixRecordType;
  record_id: string;
  run_id: string;
  signed_amount_paise: number;
  occurred_at_utc: string | null;
  content_hash: string | null;
  source_row_number: number;
  link_state: MatrixLinkState;
  match_rule: string | null;
  missing_links: string[];

  status?: string;
  order_id?: string | null;
  payment_id?: string | null;
  gross_amount_paise?: number;
  fee_paise?: number;
  tax_paise?: number;
  net_amount_paise?: number;
  refund_amount_paise?: number;
  settlement_id?: string | null;
  settlement_gross_paise?: number | null;
  gross_credit_paise?: number;
  adjustment_paise?: number;
  window_start_utc?: string;
  window_end_utc?: string;
  utr?: string | null;
  bank_entry_id?: string | null;
  bank_amount_paise?: number | null;
  value_date?: string;
  narration?: string | null;
  account_fingerprint?: string | null;
  ledger_entry_id?: string | null;
  ledger_amount_paise?: number | null;
  account_code?: string | null;
  source_reference?: string | null;
  source_type?: string | null;
  entry_origin?: string | null;
  description?: string | null;
}

export interface MatrixCensusEntry {
  total: number;
  reconciled: number;
  unmatched: number;
}

export interface MatrixInventory {
  total_records: number;
  reconciled_records: number;
  unmatched_records: number;
  by_record_type: Record<string, MatrixCensusEntry>;
}

export interface MatrixPage {
  run_id: string;
  total: number;
  page: number;
  limit: number;
  total_pages: number;
  record_type: string;
  link_state: string;
  search: string;
  inventory: MatrixInventory;
  records: MatrixRecord[];
}

/* ------------------------------------------------------------------ */
/* MDR & GST audit                                                     */
/* ------------------------------------------------------------------ */

/** The configured SYNTHETIC merchant fee policy that produced an audit. */
export interface FeePolicyView {
  policy_id: string;
  policy_version: string;
  policy_fingerprint: string;
  mdr_bps: number;
  gst_on_fee_bps: number;
  tolerance_paise: number;
  rounding_rule: string;
  data_classification: string;
  source: string;
  notice: string;
}

/* ------------------------------------------------------------------ */
/* Integration status                                                  */
/* ------------------------------------------------------------------ */

export type IntegrationState = "NOT_CONFIGURED" | "CONFIGURED" | "REACHABLE" | "FAILED";

export interface IntegrationStatusItem {
  name: string;
  label: string;
  configured: boolean;
  state: IntegrationState;
  probe_performed: boolean;
  probe_ok: boolean | null;
  probe_reason: string | null;
  last_checked_utc: string | null;
  probeable: boolean;
  detail: Record<string, unknown>;
}

export interface IntegrationStatusResponse {
  observed_at_utc: string;
  probed: string[];
  notice: string;
  integrations: IntegrationStatusItem[];
}
