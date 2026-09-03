import { describe, expect, it } from "vitest";
import {
  buildDemoView,
  buildGatewayView,
  capturedPaymentCount,
  describeDossierPage,
  type EntityCounts,
  type GatewayDemoEvidence,
  type GatewayImportDetail,
  type GatewayPaymentDossierItem,
  type RazorpaySyncResult,
} from "../../src/lib/gateway-view";

function dossier(count: number): GatewayPaymentDossierItem[] {
  return Array.from({ length: count }, (_unused, index) => ({
    payment_id: `pay_${index}`,
    order_id: `order_${index}`,
    status: "captured",
    currency: "INR",
    amount_paise: 10000 + index,
    created_at: 1772437000 + index,
    readiness_state: "AWAITING_RAZORPAY_SETTLEMENT",
  }));
}

function paymentCounts(overrides: Partial<EntityCounts> = {}): EntityCounts {
  return {
    total: 7,
    captured: 7,
    eligible: 7,
    awaiting_settlement: 7,
    settlement_available: 0,
    not_eligible: 0,
    ...overrides,
  };
}

function refundCounts(overrides: Partial<EntityCounts> = {}): EntityCounts {
  return {
    total: 2,
    processed: 2,
    eligible: 2,
    awaiting_settlement: 2,
    settlement_available: 0,
    not_eligible: 0,
    ...overrides,
  };
}

const SYNC: RazorpaySyncResult = {
  orders_count: 9,
  payments_count: 7,
  refunds_count: 2,
  settlements_count: 0,
  settlement_reconciliation_count: 0,
  source_records_count: 18,
  reconciliation_eligible_count: 9,
  import_id: "gwi-live",
  message: "live message",
  gateway_ready: false,
  settlement_reconciliation_required: true,
  credentials_persisted: false,
  lifecycle_state: "AWAITING_RAZORPAY_SETTLEMENT",
  readiness_counts: { AWAITING_RAZORPAY_SETTLEMENT: 9 },
  payment_dossier: dossier(7),
  payment_dossier_total: 7,
  payment_dossier_limit: 25,
  payment_dossier_offset: 0,
  payment_dossier_truncated: false,
  payment_counts: paymentCounts(),
  refund_counts: refundCounts(),
  imported_at_utc: "2026-09-03T04:00:00+00:00",
};

const DETAIL: GatewayImportDetail = {
  import_id: "gwi-restored",
  provider: "RAZORPAY",
  mode: "TEST",
  status: "STAGED",
  source_records_count: 18,
  reconciliation_eligible_count: 9,
  counts: { ORDER: 9, PAYMENT: 130, REFUND: 2 },
  imported_at_utc: "2026-09-03T04:00:00+00:00",
  readiness_counts: { AWAITING_RAZORPAY_SETTLEMENT: 9 },
  payment_dossier: dossier(25),
  payment_dossier_total: 130,
  payment_dossier_limit: 25,
  payment_dossier_offset: 0,
  payment_dossier_truncated: true,
  payment_counts: paymentCounts({ total: 130, captured: 130, eligible: 130, awaiting_settlement: 130 }),
  refund_counts: refundCounts(),
  excluded: [{ entity_type: "ORDER", reason: "ORDER_IS_NOT_A_PAYMENT", count: 9 }],
  demo_evidence: null,
};

const DEMO_ACTIVE: GatewayDemoEvidence = {
  evidence_id: "demo-abc123",
  manifest_hash: "f".repeat(64),
  created_at_utc: "2026-09-03T05:00:00+00:00",
  provenance: "SYNTHETIC_DEMO",
  production_eligible: false,
  activation_state: "ACTIVE",
  active_demo_sources: ["payments", "refunds", "settlements", "bank_entries", "ledger_entries"],
  superseded_sources: [],
  expected_sources: ["payments", "refunds", "settlements", "bank_entries", "ledger_entries"],
};

describe("gateway-only demo scope", () => {
  const evidence: GatewayDemoEvidence = {
    ...DEMO_ACTIVE,
    scope: "GATEWAY_ONLY",
    active_demo_sources: ["payments", "refunds", "settlements"],
    expected_sources: ["payments", "refunds", "settlements"],
  };

  it("separates synthetic gateway evidence from the two merchant uploads", () => {
    const detail = { ...DETAIL, demo_evidence: evidence };
    const restored = buildDemoView(null, detail, DETAIL.import_id);
    expect(restored?.heading).toBe("Synthetic gateway evidence active");
    expect(restored?.message).toContain("3 synthetic gateway sources active");
    expect(restored?.message).toContain("No bank or merchant ledger file was generated or replaced");
    expect(restored?.message).toContain("Official API counts are unchanged");
    const fresh = buildDemoView({
      importId: DETAIL.import_id, evidence_id: evidence.evidence_id,
      provenance: "SYNTHETIC_DEMO", message: "generated",
    }, detail, DETAIL.import_id);
    expect(fresh?.heading).toBe(restored?.heading);
    expect(fresh?.message).toBe(restored?.message);
  });

  it("keeps official counters separate even when all workflow sources are ready", () => {
    const view = buildGatewayView(null, { ...DETAIL, demo_evidence: evidence }, {
      gatewayImportId: DETAIL.import_id, settlementReconciliationRequired: false,
    });
    expect(view?.workflowSettlementReady).toBe(true);
    expect(view?.settlementsCount).toBe(0);
    expect(view?.reconciliationCount).toBe(0);
    expect(view?.ordersCount).toBe(9);
    expect(view?.paymentCounts.total).toBe(130);
    expect(view?.refundCounts.total).toBe(2);
    expect(view?.officialSettlementRowsReturned).toBe(false);
  });

  it("does not silently re-label historical full-demo bundles as gateway-only", () => {
    const view = buildDemoView(null, { ...DETAIL, demo_evidence: { ...DEMO_ACTIVE, scope: "FULL_DEMO" } }, DETAIL.import_id);
    expect(view?.heading).toBe("Synthetic demo chain active");
    expect(view?.message).toContain("legacy bundle included bank and ledger files");
    expect(view?.message).toContain("do not satisfy the separate merchant-upload requirement");
  });
});

describe("buildGatewayView", () => {
  it("returns null only when no import exists at all", () => {
    expect(buildGatewayView(null, null, null)).toBeNull();
  });

  it("rebuilds the intake card from the persisted snapshot after a refresh", () => {
    const view = buildGatewayView(null, DETAIL, {
      gatewayImportId: "gwi-restored",
      settlementReconciliationRequired: true,
    });
    expect(view?.restored).toBe(true);
    expect(view?.importId).toBe("gwi-restored");
    expect(view?.ordersCount).toBe(9);
    expect(view?.paymentCounts.total).toBe(130);
    expect(view?.refundCounts.total).toBe(2);
    expect(view?.dossierTotal).toBe(130);
    expect(view?.dossierTruncated).toBe(true);
    expect(view?.message).toContain("gwi-restored");
    expect(view?.message).toContain("Credentials were never persisted");
  });

  // REVIEW-005: current session readiness must outrank the sync response.
  it("lets current session readiness govern a fresh sync, not the stale response", () => {
    const view = buildGatewayView(SYNC, null, {
      gatewayImportId: "gwi-live",
      settlementReconciliationRequired: false,
    });
    // The sync response said settlement was still required; the session has
    // since become ready (for example via demo evidence). Current wins.
    expect(view?.workflowSettlementReady).toBe(true);
    expect(view?.readinessConfirmed).toBe(true);
    // The immutable gateway fact is unchanged: Razorpay issued no settlement.
    expect(view?.officialSettlementRowsReturned).toBe(false);
  });

  it("does not apply readiness that describes a different import", () => {
    const view = buildGatewayView(SYNC, null, {
      gatewayImportId: "gwi-someone-else",
      settlementReconciliationRequired: false,
    });
    // Falls back to the response's own fact and says so, rather than
    // asserting a readiness it cannot vouch for.
    expect(view?.workflowSettlementReady).toBe(false);
    expect(view?.readinessConfirmed).toBe(false);
  });

  it("keeps official settlement presence separate from workflow readiness", () => {
    const withOfficial = buildGatewayView(
      { ...SYNC, settlements_count: 2, settlement_reconciliation_count: 5 },
      null,
      { gatewayImportId: "gwi-live", settlementReconciliationRequired: false },
    );
    expect(withOfficial?.officialSettlementRowsReturned).toBe(true);
    expect(withOfficial?.workflowSettlementReady).toBe(true);

    const syntheticOnly = buildGatewayView(SYNC, null, {
      gatewayImportId: "gwi-live",
      settlementReconciliationRequired: false,
    });
    // Ready to proceed, but Razorpay issued nothing. Both facts survive.
    expect(syntheticOnly?.officialSettlementRowsReturned).toBe(false);
    expect(syntheticOnly?.workflowSettlementReady).toBe(true);
  });

  it("prefers the live import result over a snapshot for a different import", () => {
    const view = buildGatewayView(SYNC, DETAIL, null);
    expect(view?.restored).toBe(false);
    expect(view?.importId).toBe("gwi-live");
    expect(view?.message).toBe("live message");
  });
});

describe("buildDemoView", () => {
  it("never borrows activation from a different evidence record for the same import", () => {
    const view = buildDemoView(
      {importId: "gwi-restored", evidence_id: "demo-new", provenance: "SYNTHETIC_DEMO", message: "new"},
      {...DETAIL, demo_evidence: DEMO_ACTIVE}, "gwi-restored",
    );
    expect(view?.activationState).toBe("UNKNOWN");
    expect(view?.activeSources).toEqual([]);
  });
  it("shows nothing without a demo record or without an active import", () => {
    expect(buildDemoView(null, DETAIL, "gwi-restored")).toBeNull();
    expect(buildDemoView(null, null, "gwi-restored")).toBeNull();
    expect(buildDemoView(null, { ...DETAIL, demo_evidence: DEMO_ACTIVE }, null)).toBeNull();
  });

  // REVIEW-003: a previous import's demo must never appear beside a new one.
  it("ignores a fresh demo generated for a different import", () => {
    const leaked = buildDemoView(
      { importId: "gwi-A", evidence_id: "demo-A", provenance: "SYNTHETIC_DEMO", message: "A" },
      DETAIL,
      "gwi-restored",
    );
    expect(leaked).toBeNull();
  });

  it("ignores persisted evidence attached to a different import", () => {
    const stale = buildDemoView(
      null,
      { ...DETAIL, import_id: "gwi-old", demo_evidence: DEMO_ACTIVE },
      "gwi-restored",
    );
    expect(stale).toBeNull();
  });

  it("restores an active bundle from persisted evidence", () => {
    const view = buildDemoView(null, { ...DETAIL, demo_evidence: DEMO_ACTIVE }, "gwi-restored");
    expect(view?.evidenceId).toBe("demo-abc123");
    expect(view?.activationState).toBe("ACTIVE");
    expect(view?.restored).toBe(true);
    expect(view?.heading).toBe("Synthetic demo chain active");
    expect(view?.message).toContain("never");
  });

  // REVIEW-002: history is not activation.
  it("reports a partially active bundle without hiding the synthetic provenance", () => {
    const view = buildDemoView(
      null,
      {
        ...DETAIL,
        demo_evidence: {
          ...DEMO_ACTIVE,
          activation_state: "PARTIALLY_ACTIVE",
          active_demo_sources: ["payments", "refunds", "bank_entries", "ledger_entries"],
          superseded_sources: ["settlements"],
        },
      },
      "gwi-restored",
    );
    expect(view?.activationState).toBe("PARTIALLY_ACTIVE");
    expect(view?.heading).toBe("Synthetic demo chain partially active");
    expect(view?.message).toContain("settlements");
    expect(view?.message).toContain("mixes synthetic");
  });

  it("reports a superseded bundle as history, not as active evidence", () => {
    const view = buildDemoView(
      null,
      {
        ...DETAIL,
        demo_evidence: {
          ...DEMO_ACTIVE,
          activation_state: "SUPERSEDED",
          active_demo_sources: [],
          superseded_sources: DEMO_ACTIVE.expected_sources,
        },
      },
      "gwi-restored",
    );
    expect(view?.activationState).toBe("SUPERSEDED");
    expect(view?.heading).toBe("Synthetic demo evidence superseded");
    expect(view?.message).toContain("audit history only");
  });

  it("refuses to claim activation when the manifest is unreadable", () => {
    const view = buildDemoView(
      null,
      { ...DETAIL, demo_evidence: { ...DEMO_ACTIVE, activation_state: "UNKNOWN" } },
      "gwi-restored",
    );
    expect(view?.heading).toBe("Synthetic demo activation unknown");
    expect(view?.message).toContain("Do not treat this session as verified");
  });

  it("uses the fresh response for the import it was generated for", () => {
    const view = buildDemoView(
      {
        importId: "gwi-restored",
        evidence_id: "demo-fresh",
        provenance: "SYNTHETIC_DEMO",
        message: "fresh",
      },
      DETAIL,
      "gwi-restored",
    );
    expect(view?.evidenceId).toBe("demo-fresh");
    expect(view?.restored).toBe(false);
    expect(view?.activationState).toBe("UNKNOWN");
    expect(view?.message).toContain("has not been confirmed");
  });

  it("lets a re-read snapshot correct a fresh response that is already superseded", () => {
    const view = buildDemoView(
      {
        importId: "gwi-restored",
        evidence_id: "demo-abc123",
        provenance: "SYNTHETIC_DEMO",
        message: "fresh",
      },
      {
        ...DETAIL,
        demo_evidence: {
          ...DEMO_ACTIVE,
          activation_state: "SUPERSEDED",
          active_demo_sources: [],
          superseded_sources: DEMO_ACTIVE.expected_sources,
        },
      },
      "gwi-restored",
    );
    expect(view?.activationState).toBe("SUPERSEDED");
    expect(view?.message).toContain("audit history only");
  });
});

describe("capturedPaymentCount", () => {
  it("counts provider-captured payments, which is not the eligible count", () => {
    // A captured payment missing fee/tax is captured but NOT eligible: the
    // demo generator also requires fee/tax; this count is NOT its eligibility.
    const counts = paymentCounts({ total: 3, captured: 2, eligible: 1, not_eligible: 2 });
    expect(capturedPaymentCount(counts)).toBe(2);
    expect(counts.eligible).toBe(1);
    expect(capturedPaymentCount(undefined)).toBe(0);
  });
});

describe("describeDossierPage", () => {
  // REVIEW-004: every figure must describe the same defined population.
  it("describes payment records, not captured payments, with payment-scoped counts", () => {
    const view = buildGatewayView(
      null,
      {
        ...DETAIL,
        payment_dossier: dossier(2),
        payment_dossier_total: 2,
        payment_dossier_truncated: false,
        // One captured pending payment and one failed payment. The all-entity
        // roll-up says 2 awaiting because it also counts a refund; the
        // payment-scoped counts say 1.
        readiness_counts: { AWAITING_RAZORPAY_SETTLEMENT: 2 },
        payment_counts: paymentCounts({
          total: 2,
          captured: 1,
          eligible: 1,
          awaiting_settlement: 1,
          not_eligible: 1,
        }),
      },
      null,
    );
    expect(describeDossierPage(view!, 4)).toBe(
      "Preview of 2 of 2 payment records in this import · 1 reconciliation-eligible · " +
        "1 awaiting Razorpay settlement · 1 not eligible.",
    );
  });

  it("reports the true total and never claims more rows than the page holds", () => {
    const view = buildGatewayView(null, DETAIL, null);
    expect(describeDossierPage(view!, 4)).toContain("Preview of 4 of 130 payment records");
    expect(describeDossierPage(view!, 999)).toContain("Preview of 25 of 130 payment records");
  });

  it("stays truthful for a single record", () => {
    const view = buildGatewayView(
      null,
      {
        ...DETAIL,
        payment_dossier: dossier(1),
        payment_dossier_total: 1,
        payment_dossier_truncated: false,
        payment_counts: paymentCounts({
          total: 1,
          captured: 1,
          eligible: 1,
          awaiting_settlement: 1,
          not_eligible: 0,
        }),
      },
      null,
    );
    expect(describeDossierPage(view!, 4)).toBe(
      "Preview of 1 of 1 payment record in this import · 1 reconciliation-eligible · " +
        "1 awaiting Razorpay settlement · 0 not eligible.",
    );
  });
});
