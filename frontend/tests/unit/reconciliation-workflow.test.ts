import { describe, expect, it } from "vitest";
import {
  canRetryWorkflow,
  INITIAL_RECONCILIATION_WORKFLOW_STATE,
  isWorkflowBusy,
  reconciliationWorkflowReducer,
  requireReconciliationJob,
  type ReconciliationJob,
  type ReconciliationWorkflowEvent,
} from "../../src/lib/reconciliation-workflow";

function job(
  id: string,
  sessionId: string,
  status: ReconciliationJob["status"] = "RUNNING",
): ReconciliationJob {
  const terminal = status === "FAILED" || status === "BLOCKED" || status === "SUCCEEDED";
  return {
    job_id: id,
    session_id: sessionId,
    status,
    phase: status,
    terminal,
    execution_mode: "rules-only",
    provider_id: "none",
    simulated: false,
    attempt_count: status === "QUEUED" ? 0 : 1,
    max_attempts: 2,
    run_id: status === "SUCCEEDED" ? "run-1" : null,
    failure_code: status === "FAILED" ? "RUN_FAILED" : null,
    failure_detail: status === "FAILED" ? "safe failure" : null,
    summary: null,
    progress: {
      kind: "STEP_COMPLETION",
      headline: status,
      detail: status,
      completed_steps: status === "SUCCEEDED" ? 4 : 0,
      total_steps: 4,
      steps: [],
    },
    recovery: {
      retryable: status === "FAILED",
      remaining_attempts: 1,
      action: status === "FAILED" ? "RETRY" : status === "SUCCEEDED" ? "OPEN_RUN" : "WAIT",
    },
  };
}

function run(events: ReconciliationWorkflowEvent[]) {
  return events.reduce(reconciliationWorkflowReducer, INITIAL_RECONCILIATION_WORKFLOW_STATE);
}

describe("reconciliation workflow state", () => {
  it("moves from starting to polling to a persisted terminal result", () => {
    const state = run([
      { type: "STARTED", requestId: 1, sessionId: "session-a" },
      { type: "JOB_RECEIVED", requestId: 1, sessionId: "session-a", job: job("job-a", "session-a") },
      { type: "JOB_RECEIVED", requestId: 1, sessionId: "session-a", job: job("job-a", "session-a", "SUCCEEDED") },
    ]);
    expect(state.clientStatus).toBe("TERMINAL");
    expect(state.job?.run_id).toBe("run-1");
    expect(isWorkflowBusy(state)).toBe(false);
  });

  it("rejects responses from an older request or another session", () => {
    const state = run([
      { type: "STARTED", requestId: 1, sessionId: "session-a" },
      { type: "STARTED", requestId: 2, sessionId: "session-b" },
      { type: "JOB_RECEIVED", requestId: 1, sessionId: "session-a", job: job("old", "session-a") },
      { type: "JOB_RECEIVED", requestId: 2, sessionId: "session-b", job: job("wrong", "session-a") },
    ]);
    expect(state.sessionId).toBe("session-b");
    expect(state.job).toBeNull();
  });

  it("never lets a different job overwrite the accepted job identity", () => {
    const state = run([
      { type: "STARTED", requestId: 1, sessionId: "session-a" },
      { type: "JOB_RECEIVED", requestId: 1, sessionId: "session-a", job: job("job-a", "session-a") },
      { type: "JOB_RECEIVED", requestId: 1, sessionId: "session-a", job: job("job-b", "session-a") },
    ]);
    expect(state.job?.job_id).toBe("job-a");
  });

  it("preserves the last durable job when status polling is temporarily unavailable", () => {
    const state = run([
      { type: "STARTED", requestId: 1, sessionId: "session-a" },
      { type: "JOB_RECEIVED", requestId: 1, sessionId: "session-a", job: job("job-a", "session-a") },
      { type: "STATUS_UNAVAILABLE", requestId: 1, sessionId: "session-a", message: "Backend unavailable" },
    ]);
    expect(state.clientStatus).toBe("STATUS_UNAVAILABLE");
    expect(state.job?.job_id).toBe("job-a");
    expect(state.statusError).toBe("Backend unavailable");
  });

  it("resets without accepting a response that was already in flight", () => {
    const state = run([
      { type: "STARTED", requestId: 1, sessionId: "session-a" },
      { type: "RESET", requestId: 2 },
      { type: "JOB_RECEIVED", requestId: 1, sessionId: "session-a", job: job("job-a", "session-a") },
    ]);
    expect(state.clientStatus).toBe("IDLE");
    expect(state.job).toBeNull();
  });

  it("allows retries only when the backend recovery contract permits one", () => {
    const retryable = job("job-a", "session-a", "FAILED");
    expect(canRetryWorkflow(retryable)).toBe(true);
    expect(canRetryWorkflow({ ...retryable, recovery: { ...retryable.recovery, retryable: false } })).toBe(false);
    expect(canRetryWorkflow(job("job-a", "session-a", "BLOCKED"))).toBe(false);
  });

  it("validates the workflow contract and its session identity before rendering", () => {
    expect(requireReconciliationJob(job("job-a", "session-a"), "session-a", "job-a").job_id).toBe("job-a");
    expect(() => requireReconciliationJob(job("job-a", "session-a"), "session-b")).toThrow(/identity or response contract/i);
    expect(() => requireReconciliationJob({ job_id: "job-a", session_id: "session-a" }, "session-a")).toThrow(/identity or response contract/i);
  });

  it("preserves a known job only for an explicit same-session status check", () => {
    const polling = run([
      { type: "STARTED", requestId: 1, sessionId: "session-a" },
      { type: "JOB_RECEIVED", requestId: 1, sessionId: "session-a", job: job("job-a", "session-a") },
      { type: "STARTED", requestId: 2, sessionId: "session-a", preserveJob: true },
    ]);
    expect(polling.job?.job_id).toBe("job-a");
    const newRequest = reconciliationWorkflowReducer(polling, {
      type: "STARTED",
      requestId: 3,
      sessionId: "session-a",
    });
    expect(newRequest.job).toBeNull();
  });
});
