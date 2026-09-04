/**
 * Shared API response shapes for the control room.
 *
 * These mirror the backend route payloads (backend/app/api/routes_*.py).
 * The UI renders API results only; it contains no financial truth logic.
 */

export interface EvidenceItem {
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
  evidence: EvidenceItem[];
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

export interface CaseDetail {
  case: CaseSummary;
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
