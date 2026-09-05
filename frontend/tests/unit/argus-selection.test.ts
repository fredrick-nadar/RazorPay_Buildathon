/**
 * Chunk 3C: the authoritative selection context.
 *
 * These tests pin the three guarantees the reducer exists to provide:
 * URL restoration, generation-based rejection of stale responses, and refusal
 * of cross-run / wrong-case / mis-ordered payloads before render.
 */

import { describe, expect, it } from "vitest";

import {
  ContractError,
  DEFAULT_SELECTION_REQUEST,
  classifyFailure,
  initialSelectionState,
  isArgusView,
  parseSelectionRequest,
  requireAuditEvents,
  requireCaseDetail,
  requireCaseList,
  requireRunView,
  selectionForUrl,
  selectionReducer,
  selectionSearchParams,
  type SelectionEvent,
  type SelectionState,
} from "../../src/lib/argus-selection";
import type { AuditLogItem, CaseDetail, CaseSummary, RunListItem } from "../../src/lib/types";

const RUN_A = "run-aaaa1111";
const RUN_B = "run-bbbb2222";
const CASE_A = "case-aaaa1111";
const CASE_B = "case-bbbb2222";

function run(runId: string): RunListItem {
  return {
    run_id: runId,
    tenant_id: "argus-demo",
    inputs_path: "datasets/dev/inputs",
    status: "COMPLETED",
    started_at_utc: "2026-09-04T10:00:00Z",
    finished_at_utc: "2026-09-04T10:00:01Z",
    economic_output_hash: "a".repeat(64),
    summary: { mode: "rules-only" },
  };
}

function caseSummary(caseId: string, runId: string): CaseSummary {
  return {
    case_id: caseId,
    run_id: runId,
    category: "DUPLICATE_LEDGER_POSTING",
    status: "APPROVAL_REQUIRED",
    variance_paise: 2116738,
    affected_amount_paise: 2116738,
    proposed_delta_paise: -2116738,
    currency: "INR",
    summary: "duplicate posting",
    reason_codes: [],
    evidence: [],
    opened_at_utc: "2026-09-04T10:00:00Z",
    updated_at_utc: "2026-09-04T10:00:00Z",
  };
}

function caseDetail(caseId: string, runId: string): CaseDetail {
  return {
    case: { ...caseSummary(caseId, runId), evidence: [] },
    hypotheses: [],
    proof: null,
    dry_run: null,
    simulated_correction: null,
    approvals: [],
  };
}

function auditEvent(sequence: number, runId: string, caseId: string | null): AuditLogItem {
  return {
    event_id: `evt-${sequence}`,
    case_id: caseId,
    run_id: runId,
    timestamp_utc: "2026-09-04T10:00:00Z",
    actor: "SYSTEM",
    action: "CASE_OPENED",
    payload: {},
    digest: "b".repeat(64),
    sequence,
  };
}

/** Drive the reducer through a loaded run with one selected case. */
function loaded(runId = RUN_A, caseId: string | null = CASE_A): SelectionState {
  const events: SelectionEvent[] = [
    { type: "RUN_REQUESTED", requestId: 1, runId, caseId },
    {
      type: "RUN_LOADED",
      requestId: 1,
      run: run(runId),
      cases: [caseSummary(CASE_A, runId), caseSummary(CASE_B, runId)],
      runAudit: [auditEvent(1, runId, null)],
    },
  ];
  if (caseId) {
    events.push(
      { type: "CASE_REQUESTED", requestId: 2, runId, caseId },
      {
        type: "CASE_LOADED",
        requestId: 2,
        runId,
        caseId,
        detail: caseDetail(caseId, runId),
        audit: [auditEvent(2, runId, caseId)],
      },
    );
  }
  return events.reduce(selectionReducer, initialSelectionState(DEFAULT_SELECTION_REQUEST));
}

describe("URL selection round trip", () => {
  it("restores view, run and case from a query string", () => {
    const request = parseSelectionRequest(`?view=ledger&run=${RUN_A}&case=${CASE_A}`);
    expect(request).toEqual({ view: "ledger", runId: RUN_A, caseId: CASE_A });
    expect(parseSelectionRequest(selectionSearchParams(request))).toEqual(request);
  });

  it("omits defaults so a plain dashboard link stays clean", () => {
    expect(selectionSearchParams({ view: "home", runId: null, caseId: null })).toBe("");
    expect(selectionSearchParams({ view: "audit", runId: null, caseId: null })).toBe("?view=audit");
  });

  it("ignores an unknown view and a malformed identifier", () => {
    // An id reaches API paths, so anything outside the minted shape is dropped
    // rather than forwarded.
    expect(parseSelectionRequest("?view=not_a_view&run=../../etc&case=%3Cscript%3E")).toEqual({
      view: "home",
      runId: null,
      caseId: null,
    });
    expect(isArgusView("ledger")).toBe(true);
    expect(isArgusView("ledgerr")).toBe(false);
  });

  it("never restores or serializes a case without its run", () => {
    expect(parseSelectionRequest(`?view=dossier&case=${CASE_A}`)).toEqual({
      view: "dossier",
      runId: null,
      caseId: null,
    });
    expect(
      selectionSearchParams({ view: "dossier", runId: null, caseId: CASE_A }),
    ).toBe("?view=dossier");
  });

  it("names the run the backend returned, not the one requested", () => {
    // Opening /dashboard with no run resolves to the active run; the URL then
    // pins that exact id so a refresh restores the same batch.
    const state = loaded();
    expect(selectionForUrl(state)).toEqual({ view: "home", runId: RUN_A, caseId: CASE_A });
  });
});

describe("stale response rejection", () => {
  it("drops a run response from a superseded generation", () => {
    const state = loaded();
    const late = selectionReducer(state, {
      type: "RUN_LOADED",
      requestId: 0,
      run: run(RUN_B),
      cases: [],
      runAudit: [],
    });
    expect(late).toBe(state);
    expect(late.run?.run_id).toBe(RUN_A);
  });

  it("drops a case response after the run selection moved on", () => {
    const state = loaded();
    const switched = selectionReducer(state, {
      type: "RUN_REQUESTED",
      requestId: 5,
      runId: RUN_B,
      caseId: null,
    });
    // The old run's case response is now meaningless.
    const late = selectionReducer(switched, {
      type: "CASE_LOADED",
      requestId: 2,
      runId: RUN_A,
      caseId: CASE_A,
      detail: caseDetail(CASE_A, RUN_A),
      audit: [],
    });
    expect(late).toBe(switched);
    expect(late.caseDetail).toBeNull();
    expect(late.run).toBeNull();
  });

  it("drops a slower response for a previously selected case", () => {
    const state = loaded();
    const switching = selectionReducer(state, {
      type: "CASE_REQUESTED",
      requestId: 3,
      runId: RUN_A,
      caseId: CASE_B,
    });
    // Selecting B clears A's dossier immediately, so A's evidence can never
    // appear under B's id.
    expect(switching.caseDetail).toBeNull();
    expect(switching.caseStatus).toBe("LOADING");

    const lateA = selectionReducer(switching, {
      type: "CASE_LOADED",
      requestId: 2,
      runId: RUN_A,
      caseId: CASE_A,
      detail: caseDetail(CASE_A, RUN_A),
      audit: [],
    });
    expect(lateA).toBe(switching);

    const currentB = selectionReducer(switching, {
      type: "CASE_LOADED",
      requestId: 3,
      runId: RUN_A,
      caseId: CASE_B,
      detail: caseDetail(CASE_B, RUN_A),
      audit: [],
    });
    expect(currentB.caseStatus).toBe("READY");
    expect(currentB.caseDetail?.case.case_id).toBe(CASE_B);
  });

  it("refuses a payload whose own identity disagrees with the request", () => {
    const state = loaded(RUN_A, null);
    const requested = selectionReducer(state, {
      type: "CASE_REQUESTED",
      requestId: 9,
      runId: RUN_A,
      caseId: CASE_A,
    });
    const mismatched = selectionReducer(requested, {
      type: "CASE_LOADED",
      requestId: 9,
      runId: RUN_A,
      caseId: CASE_A,
      // The envelope says CASE_A; the body describes CASE_B.
      detail: caseDetail(CASE_B, RUN_A),
      audit: [],
    });
    expect(mismatched).toBe(requested);
    expect(mismatched.caseDetail).toBeNull();
  });
});

describe("fail-closed states", () => {
  it("clears the previous run rather than presenting it as current", () => {
    const state = loaded();
    const failed = selectionReducer(state, {
      type: "RUN_FAILED",
      requestId: state.runRequestId,
      status: "UNAVAILABLE",
      code: "BACKEND_UNREACHABLE",
    });
    expect(failed.status).toBe("UNAVAILABLE");
    expect(failed.run).toBeNull();
    expect(failed.cases).toEqual([]);
    expect(failed.caseDetail).toBeNull();
    expect(failed.runAudit).toEqual([]);
    expect(failed.errorCode).toBe("BACKEND_UNREACHABLE");
  });

  it("separates a legitimately empty database from a failure", () => {
    const requested = selectionReducer(initialSelectionState(DEFAULT_SELECTION_REQUEST), {
      type: "RUN_REQUESTED",
      requestId: 1,
      runId: null,
      caseId: null,
    });
    const empty = selectionReducer(requested, { type: "RUN_EMPTY", requestId: 1 });
    expect(empty.status).toBe("EMPTY");
    expect(empty.errorCode).toBeNull();
    expect(empty.request.runId).toBeNull();
  });

  it("keeps a failed case from showing the previously open dossier", () => {
    const state = loaded();
    const switching = selectionReducer(state, {
      type: "CASE_REQUESTED",
      requestId: 3,
      runId: RUN_A,
      caseId: CASE_B,
    });
    const failed = selectionReducer(switching, {
      type: "CASE_FAILED",
      requestId: 3,
      runId: RUN_A,
      caseId: CASE_B,
      status: "NOT_FOUND",
      code: "RUN_OR_CASE_NOT_FOUND",
    });
    expect(failed.caseStatus).toBe("NOT_FOUND");
    expect(failed.caseDetail).toBeNull();
    expect(failed.caseAudit).toEqual([]);
  });

  it("classifies 404 and 409 as selection problems, not outages", () => {
    expect(classifyFailure(new Error("x"), 404)).toEqual({
      status: "NOT_FOUND",
      code: "RUN_OR_CASE_NOT_FOUND",
    });
    expect(classifyFailure(new Error("x"), 409)).toEqual({
      status: "NOT_FOUND",
      code: "SELECTION_CONFLICT",
    });
    expect(classifyFailure(new ContractError("AUDIT_ORDER_INVALID", "x"))).toEqual({
      status: "UNAVAILABLE",
      code: "AUDIT_ORDER_INVALID",
    });
    expect(classifyFailure(new Error("network"))).toEqual({
      status: "UNAVAILABLE",
      code: "BACKEND_UNREACHABLE",
    });
  });
});

describe("response contract validation", () => {
  it("accepts a well-formed run and rejects a different one", () => {
    expect(requireRunView(run(RUN_A), RUN_A).run_id).toBe(RUN_A);
    expect(() => requireRunView(run(RUN_B), RUN_A)).toThrowError(ContractError);
    expect(() => requireRunView({ run_id: RUN_A }, RUN_A)).toThrowError(ContractError);
    expect(() => requireRunView(null, RUN_A)).toThrowError(ContractError);
  });

  it("rejects a case list that mixes runs", () => {
    expect(requireCaseList([caseSummary(CASE_A, RUN_A)], RUN_A)).toHaveLength(1);
    expect(() =>
      requireCaseList([caseSummary(CASE_A, RUN_A), caseSummary(CASE_B, RUN_B)], RUN_A),
    ).toThrowError(/does not belong to the selected run/);
  });

  it("rejects a wrong-case and a cross-run dossier", () => {
    expect(requireCaseDetail(caseDetail(CASE_A, RUN_A), RUN_A, CASE_A).case.case_id).toBe(CASE_A);
    expect(() => requireCaseDetail(caseDetail(CASE_B, RUN_A), RUN_A, CASE_A)).toThrowError(
      /different case/,
    );
    expect(() => requireCaseDetail(caseDetail(CASE_A, RUN_B), RUN_A, CASE_A)).toThrowError(
      /does not belong to the selected run/,
    );
  });

  it("requires the audit trail to arrive in authoritative order", () => {
    const ordered = [auditEvent(1, RUN_A, CASE_A), auditEvent(2, RUN_A, CASE_A)];
    expect(requireAuditEvents(ordered, { runId: RUN_A, caseId: CASE_A })).toHaveLength(2);

    const reordered = [auditEvent(2, RUN_A, CASE_A), auditEvent(1, RUN_A, CASE_A)];
    expect(() => requireAuditEvents(reordered, { runId: RUN_A })).toThrowError(/append order/);

    const duplicated = [auditEvent(1, RUN_A, CASE_A), auditEvent(1, RUN_A, CASE_A)];
    expect(() => requireAuditEvents(duplicated, { runId: RUN_A })).toThrowError(/append order/);
  });

  it("rejects an audit trail carrying out-of-scope events", () => {
    const leaked = [auditEvent(1, RUN_A, CASE_A), auditEvent(2, RUN_A, CASE_B)];
    expect(() => requireAuditEvents(leaked, { runId: RUN_A, caseId: CASE_A })).toThrowError(
      /outside the case scope/,
    );
    const crossRun = [auditEvent(1, RUN_B, CASE_A)];
    expect(() => requireAuditEvents(crossRun, { runId: RUN_A })).toThrowError(
      /outside the run scope/,
    );
  });

  it("rejects an audit event with no sequence", () => {
    const legacy = [{ ...auditEvent(1, RUN_A, CASE_A), sequence: undefined }];
    expect(() => requireAuditEvents(legacy, { runId: RUN_A })).toThrowError(/malformed/);
  });
});
