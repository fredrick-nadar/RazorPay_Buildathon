/**
 * Identity-safe client state for one persisted reconciliation workflow.
 *
 * The backend owns financial execution and stage truth. This reducer only
 * decides whether an asynchronous response is still allowed to affect the
 * currently open import session.
 */

export type ReconciliationStatus = "BLOCKED" | "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
export type ReconciliationStepState = "PENDING" | "ACTIVE" | "COMPLETE" | "FAILED";
export type ReconciliationRecoveryAction =
  | "COMPLETE_INPUTS"
  | "RETRY"
  | "START_NEW_REQUEST"
  | "REVIEW_INPUTS_OR_CONFIGURATION"
  | "OPEN_RUN"
  | "WAIT";

export interface ReconciliationStep {
  code: string;
  label: string;
  detail: string;
  state: ReconciliationStepState;
}

export interface ReconciliationJob {
  job_id: string;
  session_id: string;
  status: ReconciliationStatus;
  phase: string;
  terminal: boolean;
  execution_mode: "rules-only" | "agent";
  provider_id: string;
  simulated: boolean;
  attempt_count: number;
  max_attempts: number;
  run_id: string | null;
  failure_code: string | null;
  failure_detail: string | null;
  summary: Record<string, unknown> | null;
  progress: {
    kind: "STEP_COMPLETION";
    headline: string;
    detail: string;
    completed_steps: number;
    total_steps: number;
    steps: ReconciliationStep[];
  };
  recovery: {
    retryable: boolean;
    remaining_attempts: number;
    action: ReconciliationRecoveryAction;
  };
}

export type WorkflowClientStatus =
  | "IDLE"
  | "STARTING"
  | "POLLING"
  | "TERMINAL"
  | "STATUS_UNAVAILABLE";

export interface ReconciliationWorkflowState {
  requestId: number;
  sessionId: string | null;
  clientStatus: WorkflowClientStatus;
  job: ReconciliationJob | null;
  statusError: string | null;
}

export const INITIAL_RECONCILIATION_WORKFLOW_STATE: ReconciliationWorkflowState = {
  requestId: 0,
  sessionId: null,
  clientStatus: "IDLE",
  job: null,
  statusError: null,
};

export type ReconciliationWorkflowEvent =
  | { type: "RESET"; requestId: number }
  | { type: "STARTED"; requestId: number; sessionId: string; preserveJob?: boolean }
  | { type: "JOB_RECEIVED"; requestId: number; sessionId: string; job: ReconciliationJob }
  | { type: "STATUS_UNAVAILABLE"; requestId: number; sessionId: string; message: string };

export function reconciliationWorkflowReducer(
  state: ReconciliationWorkflowState,
  event: ReconciliationWorkflowEvent,
): ReconciliationWorkflowState {
  if (event.type === "RESET") {
    if (event.requestId <= state.requestId) return state;
    return { ...INITIAL_RECONCILIATION_WORKFLOW_STATE, requestId: event.requestId };
  }
  if (event.type === "STARTED") {
    if (event.requestId <= state.requestId) return state;
    return {
      requestId: event.requestId,
      sessionId: event.sessionId,
      clientStatus: "STARTING",
      job: event.preserveJob && state.sessionId === event.sessionId ? state.job : null,
      statusError: null,
    };
  }
  if (
    event.requestId !== state.requestId ||
    event.sessionId !== state.sessionId
  ) {
    return state;
  }
  if (event.type === "STATUS_UNAVAILABLE") {
    return {
      ...state,
      clientStatus: "STATUS_UNAVAILABLE",
      statusError: event.message,
    };
  }
  if (event.job.session_id !== event.sessionId) return state;
  if (state.job && state.job.job_id !== event.job.job_id) return state;
  return {
    ...state,
    clientStatus: event.job.terminal ? "TERMINAL" : "POLLING",
    job: event.job,
    statusError: null,
  };
}

export function reconciliationJobStorageKey(sessionId: string): string {
  return `argus_reconciliation_job_v1:${sessionId}`;
}

export function isWorkflowBusy(state: ReconciliationWorkflowState): boolean {
  return state.clientStatus === "STARTING" || state.clientStatus === "POLLING";
}

export function canRetryWorkflow(job: ReconciliationJob | null): boolean {
  return Boolean(
    job?.status === "FAILED" &&
      job.recovery.action === "RETRY" &&
      job.recovery.retryable &&
      job.recovery.remaining_attempts > 0,
  );
}

const JOB_STATUSES = new Set<ReconciliationStatus>([
  "BLOCKED",
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
]);
const STEP_STATES = new Set<ReconciliationStepState>(["PENDING", "ACTIVE", "COMPLETE", "FAILED"]);
const RECOVERY_ACTIONS = new Set<ReconciliationRecoveryAction>([
  "COMPLETE_INPUTS",
  "RETRY",
  "START_NEW_REQUEST",
  "REVIEW_INPUTS_OR_CONFIGURATION",
  "OPEN_RUN",
  "WAIT",
]);

/** Reject a malformed or cross-session API response before React can render it. */
export function requireReconciliationJob(
  value: unknown,
  expectedSessionId: string,
  expectedJobId?: string,
): ReconciliationJob {
  if (!value || typeof value !== "object") {
    throw new Error("The reconciliation service returned an invalid workflow response.");
  }
  const candidate = value as Partial<ReconciliationJob>;
  const validProgress =
    candidate.progress?.kind === "STEP_COMPLETION" &&
    typeof candidate.progress.headline === "string" &&
    typeof candidate.progress.detail === "string" &&
    typeof candidate.progress.completed_steps === "number" &&
    typeof candidate.progress.total_steps === "number" &&
    Array.isArray(candidate.progress.steps) &&
    candidate.progress.steps.every(
      (step) =>
        typeof step?.code === "string" &&
        typeof step.label === "string" &&
        typeof step.detail === "string" &&
        STEP_STATES.has(step.state),
    );
  const validRecovery =
    typeof candidate.recovery?.retryable === "boolean" &&
    typeof candidate.recovery.remaining_attempts === "number" &&
    typeof candidate.recovery.action === "string" &&
    RECOVERY_ACTIONS.has(candidate.recovery.action as ReconciliationRecoveryAction);
  if (
    typeof candidate.job_id !== "string" ||
    candidate.session_id !== expectedSessionId ||
    typeof candidate.status !== "string" ||
    !JOB_STATUSES.has(candidate.status as ReconciliationStatus) ||
    typeof candidate.terminal !== "boolean" ||
    !validProgress ||
    !validRecovery ||
    (expectedJobId !== undefined && candidate.job_id !== expectedJobId)
  ) {
    throw new Error("The reconciliation workflow identity or response contract was invalid.");
  }
  return candidate as ReconciliationJob;
}
