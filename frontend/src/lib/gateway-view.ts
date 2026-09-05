/**
 * Gateway import view derivation.
 *
 * A gateway import lives in the backend, not in component memory. The dialog
 * may show it either from the response to a fresh Razorpay call or, after a
 * refresh, from the persisted snapshot re-read through the API. These helpers
 * keep both paths on one shape so no surface can quietly lose the import, the
 * SYNTHETIC_DEMO label, or the true record count.
 *
 * Two rules hold throughout:
 *   1. Every derived value is scoped to one import id, so state belonging to a
 *      previous import can never be shown beside a different one.
 *   2. Immutable gateway facts and current session readiness are kept apart. A
 *      snapshot cannot change; readiness can, and the newer one wins.
 */

export interface GatewayPaymentDossierItem {
  payment_id: string;
  order_id: string;
  status: string;
  currency: string;
  amount_paise: number;
  created_at: number;
  readiness_state: string;
}

/** Counts scoped to ONE entity type. Never mix these across populations. */
export interface EntityCounts {
  total: number;
  /** Rows whose provider status is the live one. Payments only. */
  captured?: number;
  /** Rows whose provider status is the live one. Refunds only. */
  processed?: number;
  /** Reconciliation-eligible rows: live status AND required fields present. */
  eligible: number;
  awaiting_settlement: number;
  settlement_available: number;
  not_eligible: number;
}

export type DemoActivationState = "ACTIVE" | "PARTIALLY_ACTIVE" | "SUPERSEDED" | "UNKNOWN";

export interface GatewayDemoEvidence {
  evidence_id: string;
  /** Missing only for legacy responses from before scoped demo generation. */
  scope?: "GATEWAY_ONLY" | "FULL_DEMO";
  manifest_hash: string;
  created_at_utc: string;
  provenance: "SYNTHETIC_DEMO";
  production_eligible: false;
  /** Derived from the CURRENT session manifest, not from this record existing. */
  activation_state: DemoActivationState;
  active_demo_sources: string[];
  superseded_sources: string[];
  expected_sources: string[];
  input_counts?: {
    refunds_excluded: number;
  } | null;
  refund_exclusions?: Array<{ refund_id: string; reason: string }>;
  synthetic_policy?: { policy_id: string; notice: string } | null;
}

/** POST /api/v1/razorpay/sync - a fresh, request-scoped credentialed import. */
export interface RazorpaySyncResult {
  orders_count: number;
  payments_count: number;
  refunds_count: number;
  settlements_count: number;
  settlement_reconciliation_count: number;
  source_records_count: number;
  reconciliation_eligible_count: number;
  import_id: string;
  message: string;
  gateway_ready: boolean;
  settlement_reconciliation_required: boolean;
  credentials_persisted: false;
  lifecycle_state: string;
  readiness_counts: Record<string, number>;
  payment_dossier: GatewayPaymentDossierItem[];
  payment_dossier_total: number;
  payment_dossier_limit: number;
  payment_dossier_offset: number;
  payment_dossier_truncated: boolean;
  payment_counts: EntityCounts;
  refund_counts: EntityCounts;
  imported_at_utc: string;
}

/** GET /api/v1/razorpay/imports/{id} - the persisted snapshot, re-readable forever. */
export interface GatewayImportDetail {
  import_id: string;
  provider: string;
  mode: string;
  status: string;
  source_records_count: number;
  reconciliation_eligible_count: number;
  counts: Record<string, number>;
  imported_at_utc: string;
  readiness_counts: Record<string, number>;
  payment_dossier: GatewayPaymentDossierItem[];
  payment_dossier_total: number;
  payment_dossier_limit: number;
  payment_dossier_offset: number;
  payment_dossier_truncated: boolean;
  payment_counts: EntityCounts;
  refund_counts: EntityCounts;
  excluded: { entity_type: string; reason: string; count: number }[];
  demo_evidence: GatewayDemoEvidence | null;
  demo_generation?: { eligible: boolean; reason: string | null };
}

export interface GatewayView {
  importId: string;
  /** True when rebuilt from the persisted snapshot rather than a live import. */
  restored: boolean;
  ordersCount: number;
  refundsCount: number;
  settlementsCount: number;
  reconciliationCount: number;
  sourceRecordsCount: number;
  reconciliationEligibleCount: number;
  paymentCounts: EntityCounts;
  refundCounts: EntityCounts;
  /**
   * Immutable gateway fact: did Razorpay itself return any settlement rows for
   * this snapshot? Unaffected by any synthetic bundle staged afterwards.
   */
  officialSettlementRowsReturned: boolean;
  /**
   * Current workflow state: does the session hold usable settlement evidence
   * right now? A labelled synthetic bundle can satisfy this while
   * officialSettlementRowsReturned stays false. The two are never conflated.
   */
  workflowSettlementReady: boolean;
  /** True when workflowSettlementReady came from authoritative session status. */
  readinessConfirmed: boolean;
  dossier: GatewayPaymentDossierItem[];
  dossierTotal: number;
  dossierTruncated: boolean;
  importedAtUtc: string;
  message: string;
}

export interface DemoView {
  evidenceId: string;
  provenance: "SYNTHETIC_DEMO";
  activationState: DemoActivationState;
  activeSources: string[];
  supersededSources: string[];
  restored: boolean;
  heading: string;
  message: string;
}

/** Current session readiness, tagged with the import it actually describes. */
export interface SessionReadiness {
  gatewayImportId: string | null;
  settlementReconciliationRequired: boolean;
}

/** A fresh demo response, tagged with the import it was generated for. */
export interface FreshDemoResult {
  importId: string;
  evidence_id: string;
  provenance: "SYNTHETIC_DEMO";
  message: string;
}

function count(counts: Record<string, number>, key: string): number {
  return counts[key] ?? 0;
}

export const EMPTY_ENTITY_COUNTS: EntityCounts = {
  total: 0,
  eligible: 0,
  awaiting_settlement: 0,
  settlement_available: 0,
  not_eligible: 0,
};

/** Provider status only. Demo eligibility comes from backend preflight. */
export function capturedPaymentCount(counts: EntityCounts | undefined): number {
  return counts?.captured ?? 0;
}

/**
 * Prefer the live import result; fall back to the persisted snapshot.
 *
 * Returns null only when neither exists - a genuinely absent import, not an
 * import the dialog forgot.
 */
export function buildGatewayView(
  sync: RazorpaySyncResult | null,
  detail: GatewayImportDetail | null,
  readiness: SessionReadiness | null,
): GatewayView | null {
  if (!sync && !detail) return null;
  const importId = sync ? sync.import_id : (detail as GatewayImportDetail).import_id;

  // Immutable gateway fact, read from a snapshot that cannot change.
  const officialSettlementRowsReturned = sync
    ? sync.settlements_count > 0 || sync.settlement_reconciliation_count > 0
    : count((detail as GatewayImportDetail).counts, "SETTLEMENT") > 0 ||
      count((detail as GatewayImportDetail).counts, "SETTLEMENT_RECON") > 0;

  // Current readiness governs the workflow, but only when the status we hold
  // actually describes THIS import. Otherwise use the response's own fact and
  // mark it unconfirmed rather than asserting a state we cannot vouch for.
  const readinessConfirmed = readiness !== null && readiness.gatewayImportId === importId;
  const workflowSettlementReady = readinessConfirmed
    ? !(readiness as SessionReadiness).settlementReconciliationRequired
    : false;

  if (sync) {
    return {
      importId,
      restored: false,
      ordersCount: sync.orders_count,
      refundsCount: sync.refunds_count,
      settlementsCount: sync.settlements_count,
      reconciliationCount: sync.settlement_reconciliation_count,
      sourceRecordsCount: sync.source_records_count,
      reconciliationEligibleCount: sync.reconciliation_eligible_count,
      paymentCounts: sync.payment_counts ?? EMPTY_ENTITY_COUNTS,
      refundCounts: sync.refund_counts ?? EMPTY_ENTITY_COUNTS,
      officialSettlementRowsReturned,
      workflowSettlementReady,
      readinessConfirmed,
      dossier: sync.payment_dossier,
      dossierTotal: sync.payment_dossier_total,
      dossierTruncated: sync.payment_dossier_truncated,
      importedAtUtc: sync.imported_at_utc,
      message: sync.message,
    };
  }

  const only = detail as GatewayImportDetail;
  return {
    importId,
    restored: true,
    ordersCount: count(only.counts, "ORDER"),
    refundsCount: count(only.counts, "REFUND"),
    settlementsCount: count(only.counts, "SETTLEMENT"),
    reconciliationCount: count(only.counts, "SETTLEMENT_RECON"),
    sourceRecordsCount: only.source_records_count,
    reconciliationEligibleCount: only.reconciliation_eligible_count,
    paymentCounts: only.payment_counts ?? EMPTY_ENTITY_COUNTS,
    refundCounts: only.refund_counts ?? EMPTY_ENTITY_COUNTS,
    officialSettlementRowsReturned,
    workflowSettlementReady,
    readinessConfirmed,
    dossier: only.payment_dossier,
    dossierTotal: only.payment_dossier_total,
    dossierTruncated: only.payment_dossier_truncated,
    importedAtUtc: only.imported_at_utc,
    message:
      `Restored import ${only.import_id} (${only.status}), captured ${only.imported_at_utc}. ` +
      `${only.source_records_count} gateway source records, ` +
      `${only.reconciliation_eligible_count} gateway-eligible. ` +
      "Credentials were never persisted; a new import needs them again.",
  };
}

const DEMO_HEADINGS: Record<DemoActivationState, string> = {
  ACTIVE: "Synthetic demo chain active",
  PARTIALLY_ACTIVE: "Synthetic demo chain partially active",
  SUPERSEDED: "Synthetic demo evidence superseded",
  UNKNOWN: "Synthetic demo activation unknown",
};

function demoBody(evidence: GatewayDemoEvidence): string {
  const generated = `Generated ${evidence.created_at_utc}.`;
  const exclusions = evidence.input_counts?.refunds_excluded
    ? ` ${evidence.input_counts.refunds_excluded} refund record(s) were excluded with recorded reasons.`
    : "";
  switch (evidence.activation_state) {
    case "ACTIVE":
      if (evidence.scope === "GATEWAY_ONLY") {
        return (
          `${generated} ${evidence.active_demo_sources.length} synthetic gateway sources active: ` +
          "payments, refunds and settlements. No bank or merchant ledger file was generated or replaced. " +
          "Upload those separately. Official API counts are unchanged; this is not Razorpay-issued settlement evidence." +
          exclusions
        );
      }
      return (
        `${generated} All ${evidence.active_demo_sources.length} generated sources are the ` +
        "active evidence for this session. Derived from Test Mode IDs, and never " +
        "Razorpay-issued evidence. This legacy bundle included bank and ledger files; " +
        "they do not satisfy the separate merchant-upload requirement."
      );
    case "PARTIALLY_ACTIVE":
      return (
        `${generated} Still active: ${evidence.active_demo_sources.join(", ")}. ` +
        `Replaced since generation: ${evidence.superseded_sources.join(", ")}. ` +
        "This session mixes synthetic and other evidence; every result stays labelled."
      );
    case "SUPERSEDED":
      return (
        `${generated} None of its sources are active any more; every one has been ` +
        "replaced. The record is kept for audit history only."
      );
    default:
      return (
        `${generated} The current evidence could not be verified, so activation cannot be ` +
        "confirmed. Do not treat this session as verified synthetic evidence."
      );
  }
}

function demoHeading(state: DemoActivationState, evidence: GatewayDemoEvidence | null): string {
  if (evidence?.scope === "GATEWAY_ONLY") {
    if (state === "ACTIVE") return "Synthetic gateway evidence active";
    if (state === "PARTIALLY_ACTIVE") return "Synthetic gateway evidence partially active";
  }
  return DEMO_HEADINGS[state];
}

/**
 * Show the demo label for THIS import only.
 *
 * A fresh generation response is used only when it was generated for the import
 * being displayed. Without that check, a previous import demo would appear
 * beside a different import. Persisted evidence arrives already scoped, but its
 * import id is verified too before it can feed the banner.
 */
export function buildDemoView(
  fresh: FreshDemoResult | null,
  detail: GatewayImportDetail | null,
  activeImportId: string | null,
): DemoView | null {
  if (activeImportId === null) return null;
  const scopedDetail = detail && detail.import_id === activeImportId ? detail : null;

  if (fresh && fresh.importId === activeImportId) {
    // Generation success is historical; only a matching refreshed record can
    // confirm current activation. Never borrow another evidence record\'s state.
    const candidate = scopedDetail?.demo_evidence;
    const evidence = candidate?.evidence_id === fresh.evidence_id ? candidate : null;
    const state: DemoActivationState = evidence ? evidence.activation_state : "UNKNOWN";
    return {
      evidenceId: fresh.evidence_id,
      provenance: fresh.provenance,
      activationState: state,
      activeSources: evidence?.active_demo_sources ?? [],
      supersededSources: evidence?.superseded_sources ?? [],
      restored: false,
      heading: demoHeading(state, evidence),
      message: evidence ? demoBody(evidence) : "Demo generation succeeded. Current activation has not been confirmed.",
    };
  }

  const evidence = scopedDetail?.demo_evidence ?? null;
  if (!evidence) return null;
  return {
    evidenceId: evidence.evidence_id,
    provenance: evidence.provenance,
    activationState: evidence.activation_state,
    activeSources: evidence.active_demo_sources,
    supersededSources: evidence.superseded_sources,
    restored: true,
    heading: demoHeading(evidence.activation_state, evidence),
    message: demoBody(evidence),
  };
}

/**
 * Honest one-line account of a dossier page.
 *
 * The dossier holds ALL payment records for the import, failed ones included,
 * so it is described as payment records rather than captured payments, and
 * every figure is payment-scoped. The all-entity readiness roll-up also counts
 * refunds and must never be used here.
 */
export function describeDossierPage(view: GatewayView, shown: number): string {
  const visible = Math.min(shown, view.dossier.length);
  const counts = view.paymentCounts ?? EMPTY_ENTITY_COUNTS;
  const noun = `payment record${view.dossierTotal === 1 ? "" : "s"}`;
  return (
    `Preview of ${visible} of ${view.dossierTotal} ${noun} in this import · ` +
    `${counts.eligible} reconciliation-eligible · ` +
    `${counts.awaiting_settlement} awaiting Razorpay settlement · ` +
    `${counts.not_eligible} not eligible.`
  );
}
