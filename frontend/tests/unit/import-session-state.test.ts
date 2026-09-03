import { describe, expect, it } from "vitest";
import type { GatewayImportDetail, RazorpaySyncResult } from "../../src/lib/gateway-view";
import {
  activeImportId,
  importSessionReducer,
  INITIAL_IMPORT_SESSION_STATE,
  type ImportSessionEvent,
  type ImportSessionState,
} from "../../src/lib/import-session-state";

const COUNTS = {
  total: 1,
  captured: 1,
  processed: 1,
  eligible: 1,
  awaiting_settlement: 1,
  settlement_available: 0,
  not_eligible: 0,
};

function sync(importId: string): RazorpaySyncResult {
  return {
    orders_count: 0,
    payments_count: 1,
    refunds_count: 0,
    settlements_count: 0,
    settlement_reconciliation_count: 0,
    source_records_count: 1,
    reconciliation_eligible_count: 1,
    import_id: importId,
    message: `synced ${importId}`,
    gateway_ready: false,
    settlement_reconciliation_required: true,
    credentials_persisted: false,
    lifecycle_state: "AWAITING_RAZORPAY_SETTLEMENT",
    readiness_counts: {},
    payment_dossier: [],
    payment_dossier_total: 1,
    payment_dossier_limit: 25,
    payment_dossier_offset: 0,
    payment_dossier_truncated: false,
    payment_counts: COUNTS,
    refund_counts: COUNTS,
    imported_at_utc: "2026-09-03T04:00:00+00:00",
  };
}

function detail(importId: string): GatewayImportDetail {
  return {
    import_id: importId,
    provider: "RAZORPAY",
    mode: "TEST",
    status: "STAGED",
    source_records_count: 1,
    reconciliation_eligible_count: 1,
    counts: { PAYMENT: 1 },
    imported_at_utc: "2026-09-03T04:00:00+00:00",
    readiness_counts: {},
    payment_dossier: [],
    payment_dossier_total: 1,
    payment_dossier_limit: 25,
    payment_dossier_offset: 0,
    payment_dossier_truncated: false,
    payment_counts: COUNTS,
    refund_counts: COUNTS,
    excluded: [],
    demo_evidence: null,
  };
}

function run(events: ImportSessionEvent[], from = INITIAL_IMPORT_SESSION_STATE): ImportSessionState {
  return events.reduce(importSessionReducer, from);
}
const load = (requestId: number, id: string | null): ImportSessionEvent => ({
  type: "REFRESH_LOADED", requestId, status: { gateway_import_id: id }, detail: id ? detail(id) : null,
});
function opened(id: string) {
  return run([{ type: "REFRESH_STARTED", requestId: 1 }, load(1, id)]);
}

describe("versioned session snapshot", () => {
  it("loads status and detail together", () => {
    const state = opened("A");
    expect(activeImportId(state)).toBe("A");
    expect(state.detail?.import_id).toBe("A");
  });
  it("rejects an old status/detail response after B", () => {
    const state = run([
      { type: "REFRESH_STARTED", requestId: 2 },
      { type: "REFRESH_STARTED", requestId: 3 },
      load(3, "B"), load(2, "A"),
    ], opened("A"));
    expect(activeImportId(state)).toBe("B");
    expect(state.detail?.import_id).toBe("B");
  });
  it("does not let stale requests become the newest epoch", () => {
    const state = run([{ type: "REFRESH_STARTED", requestId: 3 }, { type: "REFRESH_STARTED", requestId: 2 }, load(2, "A")]);
    expect(state.requestId).toBe(3);
    expect(state.detail).toBeNull();
  });
  it("rejects mismatched detail and link", () => {
    const state = run([{ type: "REFRESH_STARTED", requestId: 1 },
      { type: "REFRESH_LOADED", requestId: 1, status: {gateway_import_id: "B"}, detail: detail("A") }]);
    expect(state.sessionStatus).toBeNull();
    expect(state.detail).toBeNull();
  });
  it("uses authoritative null instead of a sync fallback", () => {
    const state = run([
      { type: "MUTATION_STARTED", requestId: 1 },
      { type: "SYNC_SUCCEEDED", requestId: 1, result: sync("A") },
      { type: "REFRESH_STARTED", requestId: 2 }, load(2, null),
    ]);
    expect(activeImportId(state)).toBeNull();
    expect(state.syncResult).toBeNull();
  });
  it("invalidates old demo and readiness at mutation start", () => {
    const state = run([{type:"DEMO_SUCCEEDED",requestId:1,evidence:{importId:"A",evidence_id:"demo-A",provenance:"SYNTHETIC_DEMO",message:"A"}},
      {type:"MUTATION_STARTED",requestId:2}],opened("A"));
    expect(state.freshDemo).toBeNull();
    expect(state.sessionStatus).toBeNull();
    expect(state.detail).toBeNull();
  });
  it("restores A after failed B without inventing an import link", () => {
    const state = run([{type:"MUTATION_STARTED",requestId:2},
      {type:"REFRESH_STARTED",requestId:3},load(3,"A")],opened("A"));
    expect(activeImportId(state)).toBe("A");
    expect(state.syncResult).toBeNull();
  });
  it("keeps successful B when older A arrives later", () => {
    const state = run([{type:"MUTATION_STARTED",requestId:2},
      {type:"SYNC_SUCCEEDED",requestId:2,result:sync("B")},
      {type:"REFRESH_STARTED",requestId:3},load(3,"B"),load(1,"A")],opened("A"));
    expect(activeImportId(state)).toBe("B");
    expect(state.syncResult?.import_id).toBe("B");
  });
  it.each(["REFRESH_LOADED","SYNC_SUCCEEDED","DEMO_SUCCEEDED"] as const)("rejects post-close %s", type => {
    const closed=run([{type:"RESET",requestId:2}],opened("A"));
    const event: ImportSessionEvent = type === "REFRESH_LOADED" ? load(1,"A") : type === "SYNC_SUCCEEDED"
      ? {type,requestId:1,result:sync("A")}
      : {type,requestId:1,evidence:{importId:"A",evidence_id:"demo-A",provenance:"SYNTHETIC_DEMO",message:"A"}};
    expect(importSessionReducer(closed,event)).toEqual(closed);
  });
  it("ignores pre-close reads even after reopening", () => {
    const state=run([{type:"RESET",requestId:2},{type:"REFRESH_STARTED",requestId:3},load(3,"B"),load(1,"A")],opened("A"));
    expect(activeImportId(state)).toBe("B");
  });
  it("fails closed while re-reading same-import activation", () => {
    const state=run([{type:"REFRESH_STARTED",requestId:2}],opened("A"));
    expect(state.detail).toBeNull();
    expect(state.sessionStatus).toBeNull();
    expect(state.refreshing).toBe(true);
  });
  it("does not revive readiness when refresh fails", () => {
    const state=run([{type:"REFRESH_STARTED",requestId:2},{type:"REFRESH_FAILED",requestId:2}],opened("A"));
    expect(state.sessionStatus).toBeNull();
    expect(state.detail).toBeNull();
    expect(state.refreshing).toBe(false);
  });
});
