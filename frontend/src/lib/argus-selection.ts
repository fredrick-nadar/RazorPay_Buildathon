/**
 * The one authoritative selection context shared by every ARGUS view.
 *
 * Before this module the dashboard kept the active view, run and case in
 * component state only and never wrote them to the URL, so a refresh reset the
 * view to Home, re-resolved the run to "latest persisted" and re-selected the
 * first case. Different views could also render different identities at once.
 *
 * This file owns three things and nothing else:
 *
 *   1. serializing and parsing the selection to and from the URL, so reopening
 *      or refreshing a page restores the same context;
 *   2. a pure reducer with monotonic request generations, so a late response
 *      can never replace a newer selection;
 *   3. runtime contract validators that reject a malformed, cross-run or
 *      wrong-case response before React is allowed to render it.
 *
 * It holds no financial truth. Amounts, rates, statuses, proofs and audit
 * ordering all come from the backend; this only decides whether a given
 * response still belongs to what the operator is looking at.
 */

import type { AuditLogItem, CaseDetail, CaseSummary, RunListItem } from "./types";

/* ------------------------------------------------------------------ */
/* Views                                                               */
/* ------------------------------------------------------------------ */

export const ARGUS_VIEWS = [
  "home",
  "matrix",
  "approval_queue",
  "verified_resolved",
  "unresolved",
  "dossier",
  "evidence",
  "ledger",
  "audit",
  "fee_audit",
  "api_status",
] as const;

export type ArgusView = (typeof ARGUS_VIEWS)[number];

const VIEW_SET = new Set<string>(ARGUS_VIEWS);

export function isArgusView(value: unknown): value is ArgusView {
  return typeof value === "string" && VIEW_SET.has(value);
}

/* ------------------------------------------------------------------ */
/* URL serialization                                                   */
/* ------------------------------------------------------------------ */

export interface SelectionRequest {
  view: ArgusView;
  /** Explicit run selection. `null` means "resolve the active run". */
  runId: string | null;
  /** Explicit case selection. `null` means "pick the first valid case". */
  caseId: string | null;
}

export const DEFAULT_SELECTION_REQUEST: SelectionRequest = {
  view: "home",
  runId: null,
  caseId: null,
};

/**
 * Identifier shape accepted from a URL.
 *
 * Deliberately narrow: an id reaches API paths and query strings, so only the
 * characters the backend actually mints are allowed through. Anything else is
 * treated as absent rather than forwarded.
 */
const ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;

function readId(params: URLSearchParams, key: string): string | null {
  const raw = params.get(key);
  return raw !== null && ID_PATTERN.test(raw) ? raw : null;
}

/** Read the selection a URL asks for, ignoring anything malformed. */
export function parseSelectionRequest(search: string): SelectionRequest {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const view = params.get("view");
  const runId = readId(params, "run");
  return {
    view: isArgusView(view) ? view : DEFAULT_SELECTION_REQUEST.view,
    runId,
    // A case identity is meaningful only inside its explicitly named run.
    caseId: runId ? readId(params, "case") : null,
  };
}

/** Serialize a selection to a query string, omitting defaults. */
export function selectionSearchParams(request: SelectionRequest): string {
  const params = new URLSearchParams();
  if (request.view !== DEFAULT_SELECTION_REQUEST.view) params.set("view", request.view);
  if (request.runId) params.set("run", request.runId);
  if (request.runId && request.caseId) params.set("case", request.caseId);
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

/* ------------------------------------------------------------------ */
/* Resolved state                                                      */
/* ------------------------------------------------------------------ */

export type SelectionStatus = "LOADING" | "READY" | "EMPTY" | "UNAVAILABLE" | "NOT_FOUND";
export type CaseStatusKind = "IDLE" | "LOADING" | "READY" | "UNAVAILABLE" | "NOT_FOUND";

export interface SelectionState {
  /** Monotonic generation for run-scoped requests. */
  runRequestId: number;
  /** Monotonic generation for case-scoped requests. */
  caseRequestId: number;
  /** What the URL asked for; the source of truth for restoring context. */
  request: SelectionRequest;
  status: SelectionStatus;
  /** The run the backend actually returned. Never a locally assumed id. */
  run: RunListItem | null;
  cases: CaseSummary[];
  caseStatus: CaseStatusKind;
  caseId: string | null;
  caseDetail: CaseDetail | null;
  caseAudit: AuditLogItem[];
  runAudit: AuditLogItem[];
  /** Safe, code-shaped reason for a failed run-scoped load. */
  errorCode: string | null;
  caseErrorCode: string | null;
}

export function initialSelectionState(request: SelectionRequest): SelectionState {
  return {
    runRequestId: 0,
    caseRequestId: 0,
    request,
    status: "LOADING",
    run: null,
    cases: [],
    caseStatus: "IDLE",
    caseId: null,
    caseDetail: null,
    caseAudit: [],
    runAudit: [],
    errorCode: null,
    caseErrorCode: null,
  };
}

export type SelectionEvent =
  | { type: "VIEW_CHANGED"; view: ArgusView }
  | { type: "RUN_REQUESTED"; requestId: number; runId: string | null; caseId: string | null }
  | {
      type: "RUN_LOADED";
      requestId: number;
      run: RunListItem;
      cases: CaseSummary[];
      runAudit: AuditLogItem[];
    }
  | { type: "RUN_EMPTY"; requestId: number }
  | { type: "RUN_FAILED"; requestId: number; status: "UNAVAILABLE" | "NOT_FOUND"; code: string }
  | { type: "CASE_REQUESTED"; requestId: number; runId: string; caseId: string }
  | { type: "CASE_CLEARED"; requestId: number }
  | {
      type: "CASE_LOADED";
      requestId: number;
      runId: string;
      caseId: string;
      detail: CaseDetail;
      audit: AuditLogItem[];
    }
  | {
      type: "CASE_FAILED";
      requestId: number;
      runId: string;
      caseId: string;
      status: "UNAVAILABLE" | "NOT_FOUND";
      code: string;
    };

/**
 * Advance the selection.
 *
 * Every response-bearing event is dropped unless it matches the generation it
 * was issued for, and case events additionally have to match the run and case
 * currently selected. That is what stops a slower response for an older run or
 * case from overwriting a newer one.
 */
export function selectionReducer(state: SelectionState, event: SelectionEvent): SelectionState {
  switch (event.type) {
    case "VIEW_CHANGED":
      if (event.view === state.request.view) return state;
      return { ...state, request: { ...state.request, view: event.view } };

    case "RUN_REQUESTED": {
      if (event.requestId <= state.runRequestId) return state;
      return {
        ...state,
        runRequestId: event.requestId,
        // A new run request invalidates every in-flight case response too.
        caseRequestId: state.caseRequestId + 1,
        request: { ...state.request, runId: event.runId, caseId: event.caseId },
        status: "LOADING",
        run: null,
        cases: [],
        runAudit: [],
        caseStatus: "IDLE",
        caseId: null,
        caseDetail: null,
        caseAudit: [],
        errorCode: null,
        caseErrorCode: null,
      };
    }

    case "RUN_LOADED": {
      if (event.requestId !== state.runRequestId) return state;
      return {
        ...state,
        status: "READY",
        run: event.run,
        cases: event.cases,
        runAudit: event.runAudit,
        // The URL now names the run the backend actually returned.
        request: { ...state.request, runId: event.run.run_id },
        errorCode: null,
      };
    }

    case "RUN_EMPTY": {
      if (event.requestId !== state.runRequestId) return state;
      return {
        ...state,
        status: "EMPTY",
        run: null,
        cases: [],
        runAudit: [],
        caseStatus: "IDLE",
        caseId: null,
        caseDetail: null,
        caseAudit: [],
        request: { ...state.request, runId: null, caseId: null },
        errorCode: null,
      };
    }

    case "RUN_FAILED": {
      if (event.requestId !== state.runRequestId) return state;
      // Fail closed: the previous run is not presented as current.
      return {
        ...state,
        status: event.status,
        run: null,
        cases: [],
        runAudit: [],
        caseStatus: "IDLE",
        caseId: null,
        caseDetail: null,
        caseAudit: [],
        errorCode: event.code,
      };
    }

    case "CASE_REQUESTED": {
      if (event.requestId <= state.caseRequestId) return state;
      if (state.run?.run_id !== event.runId) return state;
      return {
        ...state,
        caseRequestId: event.requestId,
        caseId: event.caseId,
        // Clear the old dossier immediately. Keeping it would show one case's
        // evidence, dry-run and audit under another case's id.
        caseDetail: null,
        caseAudit: [],
        caseStatus: "LOADING",
        caseErrorCode: null,
        request: { ...state.request, caseId: event.caseId },
      };
    }

    case "CASE_CLEARED": {
      if (event.requestId <= state.caseRequestId) return state;
      return {
        ...state,
        caseRequestId: event.requestId,
        caseId: null,
        caseDetail: null,
        caseAudit: [],
        caseStatus: "IDLE",
        caseErrorCode: null,
        request: { ...state.request, caseId: null },
      };
    }

    case "CASE_LOADED": {
      if (event.requestId !== state.caseRequestId) return state;
      if (state.run?.run_id !== event.runId || state.caseId !== event.caseId) return state;
      if (event.detail.case.case_id !== event.caseId) return state;
      if (event.detail.case.run_id !== event.runId) return state;
      return {
        ...state,
        caseStatus: "READY",
        caseDetail: event.detail,
        caseAudit: event.audit,
        caseErrorCode: null,
      };
    }

    case "CASE_FAILED": {
      if (event.requestId !== state.caseRequestId) return state;
      if (state.run?.run_id !== event.runId || state.caseId !== event.caseId) return state;
      return {
        ...state,
        caseStatus: event.status,
        caseDetail: null,
        caseAudit: [],
        caseErrorCode: event.code,
      };
    }

    default:
      return state;
  }
}

/** The selection to write into the URL for the current state. */
export function selectionForUrl(state: SelectionState): SelectionRequest {
  return {
    view: state.request.view,
    runId: state.run?.run_id ?? state.request.runId,
    caseId: state.caseId ?? state.request.caseId,
  };
}

/* ------------------------------------------------------------------ */
/* Response contract validation                                        */
/* ------------------------------------------------------------------ */

export class ContractError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = "ContractError";
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Accept a run only when it carries the fields every view depends on. */
export function requireRunView(value: unknown, expectedRunId?: string | null): RunListItem {
  if (!isRecord(value)) {
    throw new ContractError("RUN_CONTRACT_INVALID", "The run response was not an object.");
  }
  const { run_id, status, started_at_utc, summary } = value;
  if (
    typeof run_id !== "string" ||
    run_id.length === 0 ||
    typeof status !== "string" ||
    typeof started_at_utc !== "string" ||
    !isRecord(summary)
  ) {
    throw new ContractError("RUN_CONTRACT_INVALID", "The run response was missing its identity.");
  }
  if (expectedRunId != null && run_id !== expectedRunId) {
    throw new ContractError(
      "RUN_IDENTITY_MISMATCH",
      `The response describes run ${run_id}, not the selected run.`,
    );
  }
  return value as unknown as RunListItem;
}

/** Accept a case list only when every row belongs to the selected run. */
export function requireCaseList(value: unknown, expectedRunId: string): CaseSummary[] {
  if (!Array.isArray(value)) {
    throw new ContractError("CASES_CONTRACT_INVALID", "The case list was not an array.");
  }
  for (const item of value) {
    if (!isRecord(item) || typeof item.case_id !== "string" || typeof item.status !== "string") {
      throw new ContractError("CASES_CONTRACT_INVALID", "A case row was missing its identity.");
    }
    if (item.run_id !== expectedRunId) {
      throw new ContractError(
        "CASES_RUN_MISMATCH",
        "The case list does not belong to the selected run.",
      );
    }
  }
  return value as CaseSummary[];
}

/** Accept a case dossier only when it is the exact case of the exact run. */
export function requireCaseDetail(
  value: unknown,
  expectedRunId: string,
  expectedCaseId: string,
): CaseDetail {
  if (!isRecord(value) || !isRecord(value.case)) {
    throw new ContractError("CASE_CONTRACT_INVALID", "The case response was not a dossier.");
  }
  const detail = value.case;
  if (detail.case_id !== expectedCaseId) {
    throw new ContractError(
      "CASE_IDENTITY_MISMATCH",
      "The response describes a different case than the selected one.",
    );
  }
  if (detail.run_id !== expectedRunId) {
    throw new ContractError(
      "CASE_RUN_MISMATCH",
      "The selected case does not belong to the selected run.",
    );
  }
  if (!Array.isArray(detail.evidence) || !Array.isArray(value.approvals)) {
    throw new ContractError("CASE_CONTRACT_INVALID", "The case dossier was incomplete.");
  }
  return value as unknown as CaseDetail;
}

/**
 * Accept an audit trail only when it is in authoritative order and in scope.
 *
 * The backend orders by its append sequence. Validating that here means a
 * reordered or cross-scope trail is refused rather than displayed as immutable
 * history.
 */
export function requireAuditEvents(
  value: unknown,
  scope: { runId?: string; caseId?: string },
): AuditLogItem[] {
  if (!Array.isArray(value)) {
    throw new ContractError("AUDIT_CONTRACT_INVALID", "The audit trail was not an array.");
  }
  let previous = -1;
  for (const item of value) {
    if (
      !isRecord(item) ||
      typeof item.event_id !== "string" ||
      typeof item.action !== "string" ||
      typeof item.actor !== "string" ||
      typeof item.digest !== "string" ||
      typeof item.sequence !== "number" ||
      !Number.isInteger(item.sequence)
    ) {
      throw new ContractError("AUDIT_CONTRACT_INVALID", "An audit event was malformed.");
    }
    if (item.sequence <= previous) {
      throw new ContractError(
        "AUDIT_ORDER_INVALID",
        "The audit trail did not arrive in append order.",
      );
    }
    previous = item.sequence;
    if (scope.caseId !== undefined && item.case_id !== scope.caseId) {
      throw new ContractError("AUDIT_SCOPE_MISMATCH", "An audit event is outside the case scope.");
    }
    if (scope.runId !== undefined && item.run_id !== scope.runId) {
      throw new ContractError("AUDIT_SCOPE_MISMATCH", "An audit event is outside the run scope.");
    }
  }
  return value as AuditLogItem[];
}

/** Map a failed fetch to the state a view should fail closed into. */
export function classifyFailure(error: unknown, httpStatus?: number): {
  status: "UNAVAILABLE" | "NOT_FOUND";
  code: string;
} {
  if (httpStatus === 404) return { status: "NOT_FOUND", code: "RUN_OR_CASE_NOT_FOUND" };
  if (httpStatus === 409) return { status: "NOT_FOUND", code: "SELECTION_CONFLICT" };
  if (error instanceof ContractError) return { status: "UNAVAILABLE", code: error.code };
  if (typeof httpStatus === "number") return { status: "UNAVAILABLE", code: `HTTP_${httpStatus}` };
  return { status: "UNAVAILABLE", code: "BACKEND_UNREACHABLE" };
}
