"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { formatINR } from "../lib/format";
import {
  buildDemoView,
  buildGatewayView,
  capturedPaymentCount,
  describeDossierPage,
  type GatewayImportDetail,
  type RazorpaySyncResult,
} from "../lib/gateway-view";
import {
  activeImportId,
  importSessionReducer,
  INITIAL_IMPORT_SESSION_STATE,
} from "../lib/import-session-state";
import { describeRunInvestigation, type RunInvestigationReport } from "../lib/run-investigation";
import {
  canRetryWorkflow,
  INITIAL_RECONCILIATION_WORKFLOW_STATE,
  isWorkflowBusy,
  reconciliationJobStorageKey,
  reconciliationWorkflowReducer,
  requireReconciliationJob,
  type ReconciliationJob,
  type ReconciliationWorkflowState,
} from "../lib/reconciliation-workflow";
import { createImportSessionId } from "../lib/session-id";
import { IconCheck, IconRazorpay, IconUpload, IconX } from "./icons";

type DocumentType = "payments" | "refunds" | "settlements" | "bank_entries" | "ledger_entries";

interface ConnectDatasetModalProps {
  open: boolean;
  onClose: () => void;
  onSyncSuccess: (runId: string, summary: Record<string, unknown> | null) => void;
}

interface MappingDecision {
  target_field: string;
  source_column: string;
  origin: "EXACT" | "ALIAS" | "GROQ";
  confidence: "HIGH" | "MEDIUM" | "LOW";
  reason: string;
}

interface CsvAnalysis {
  filename: string;
  document_type: DocumentType;
  source_sha256: string;
  row_count: number;
  headers: string[];
  mappings: MappingDecision[];
  required_fields: string[];
  missing_required_fields: string[];
  missing_optional_fields: string[];
  warnings: string[];
  status: "READY" | "REVIEW_REQUIRED";
  mapping_provider: "DETERMINISTIC" | "GROQ_ASSISTED";
  groq_configured: boolean;
}

interface PendingCsv {
  filename: string;
  content: string;
  fileType: DocumentType;
  analysis: CsvAnalysis;
}

interface ActiveSource {
  revision_id: string;
  revision_number: number;
  source_type: DocumentType;
  original_filename: string;
  origin: string;
  external_import_id: string | null;
  row_count: number;
  accepted_count: number;
  quarantined_count: number;
  canonical_sha256: string;
}

interface SessionStatus {
  ready: boolean;
  gateway_ready: boolean;
  bank_ready: boolean;
  ledger_ready: boolean;
  ready_source_groups: number;
  settlement_reconciliation_required: boolean;
  active_sources: Partial<Record<DocumentType, ActiveSource>>;
  revision_counts: Partial<Record<DocumentType, number>>;
  lifecycle_state: string;
  gateway_import_id: string | null;
  merchant_upload_required?: DocumentType[];
}

interface DemoEvidenceResult {
  evidence_id: string;
  provenance: "SYNTHETIC_DEMO";
  production_eligible: false;
  reused: boolean;
  message: string;
}

type ReconciliationMode = "rules-only" | "agent" | "fake";

interface AiStatus {
  chain: string[];
  investigator: "live" | "fake-deterministic-v1" | "unavailable";
  live_available: boolean;
  fake_selected: boolean;
}

const IMPORT_SESSION_KEY = "argus_import_session_v1";

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function utcDateOffset(days: number): string {
  const value = new Date();
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

const TARGET_FIELDS: Record<DocumentType, string[]> = {
  payments: ["payment_id", "order_id", "status", "currency", "gross_amount", "fee_amount", "tax_amount", "captured_at_utc", "settlement_id"],
  refunds: ["refund_id", "payment_id", "status", "currency", "refund_amount", "created_at_utc", "settlement_id"],
  settlements: ["settlement_id", "settled_at_utc", "window_start_utc", "window_end_utc", "status", "currency", "gross_credit", "fee_amount", "tax_amount", "adjustment_amount", "net_amount", "utr"],
  bank_entries: ["bank_entry_id", "posted_at_utc", "value_date", "currency", "signed_amount", "narration", "utr", "account_fingerprint"],
  ledger_entries: ["ledger_entry_id", "account_code", "accounting_date", "currency", "signed_amount", "source_reference", "source_type", "description", "entry_origin"],
};

const LABELS: Record<DocumentType, string> = {
  payments: "Razorpay payments",
  refunds: "Razorpay refunds",
  settlements: "Razorpay settlements",
  bank_entries: "Bank statement",
  ledger_entries: "Merchant ledger",
};

function ApiError({ message }: { message: string }) {
  return (
    <div role="alert" className="rounded-xl border border-slate-300 bg-slate-100 px-3 py-2 text-xs font-medium text-slate-800">
      {message}
    </div>
  );
}

function StatusMark({ ready, label }: { ready: boolean; label: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[10px] font-bold uppercase tracking-[0.14em] ${ready ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 bg-slate-50 text-slate-500"}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${ready ? "bg-white" : "bg-slate-300"}`} />
      {label}
    </span>
  );
}

function WorkflowProgress({
  state,
  onRetry,
  onResume,
}: {
  state: ReconciliationWorkflowState;
  onRetry: () => void;
  onResume: () => void;
}) {
  if (state.clientStatus === "IDLE") return null;
  const job = state.job;

  if (state.clientStatus === "STARTING" && !job) {
    return (
      <section aria-live="polite" className="rounded-xl border border-slate-300 bg-white px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="h-2 w-2 animate-pulse rounded-full bg-slate-950" />
          <div>
            <p className="text-xs font-bold text-slate-950">Saving reconciliation workflow</p>
            <p className="mt-0.5 text-[11px] text-slate-500">Pinning the current evidence before work begins.</p>
          </div>
        </div>
      </section>
    );
  }

  if (state.clientStatus === "STATUS_UNAVAILABLE") {
    return (
      <section role="alert" className="rounded-xl border border-slate-400 bg-white px-4 py-3 text-slate-900">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-xl">
            <p className="text-xs font-bold">Workflow status temporarily unavailable</p>
            <p className="mt-1 text-[11px] leading-5 text-slate-600">{state.statusError}</p>
            {job && <p className="mt-1 font-mono text-[9px] uppercase tracking-wider text-slate-500">Saved job {job.job_id} · the backend remains authoritative</p>}
          </div>
          <button type="button" onClick={onResume} className="rounded-lg border border-slate-900 bg-slate-950 px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-white hover:bg-slate-800">
            {job ? "Check saved workflow" : "Try again"}
          </button>
        </div>
      </section>
    );
  }

  if (!job) return null;
  const activeOrFailed = job.progress.steps.find(
    (step) => step.state === "ACTIVE" || step.state === "FAILED",
  );
  const retryable = canRetryWorkflow(job);
  const recoveryGuidance =
    job.recovery.action === "COMPLETE_INPUTS"
      ? "Complete or replace the required evidence above; the blocked job itself will not run."
      : job.recovery.action === "START_NEW_REQUEST"
        ? "The investigator configuration changed. Start a new workflow with the current policy."
        : job.recovery.action === "REVIEW_INPUTS_OR_CONFIGURATION"
          ? "The retry limit is closed. Change the relevant evidence or configuration before starting again."
          : null;

  return (
    <section aria-live="polite" className="overflow-hidden rounded-xl border border-slate-300 bg-white text-slate-900">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 px-4 py-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs font-bold">{job.progress.headline}</p>
            <span className="rounded-full border border-slate-300 bg-slate-50 px-2 py-0.5 font-mono text-[9px] font-bold uppercase tracking-wider text-slate-600">{job.status}</span>
          </div>
          <p className="mt-1 text-[11px] leading-5 text-slate-500">{job.progress.detail}</p>
        </div>
        <p className="font-mono text-[9px] uppercase tracking-wider text-slate-500">
          {job.progress.completed_steps}/{job.progress.total_steps} stages complete
        </p>
      </div>
      <ol aria-label="Reconciliation stages" className={`grid gap-px bg-slate-200 sm:grid-cols-2 ${job.progress.total_steps === 5 ? "lg:grid-cols-5" : "lg:grid-cols-4"}`}>
        {job.progress.steps.map((step, index) => (
          <li key={step.code} className="flex min-w-0 gap-2 bg-white px-3 py-2.5">
            <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border font-mono text-[9px] font-bold ${step.state === "COMPLETE" ? "border-slate-950 bg-slate-950 text-white" : step.state === "ACTIVE" ? "border-slate-950 bg-white text-slate-950" : step.state === "FAILED" ? "border-slate-950 bg-slate-200 text-slate-950" : "border-slate-200 bg-slate-50 text-slate-400"}`}>
              {step.state === "COMPLETE" ? "✓" : index + 1}
            </span>
            <div className="min-w-0">
              <p className={`text-[10px] font-semibold leading-4 ${step.state === "PENDING" ? "text-slate-400" : "text-slate-800"}`}>{step.label}</p>
              <span className="sr-only">{step.state.toLowerCase()}</span>
            </div>
          </li>
        ))}
      </ol>
      {(job.status === "FAILED" || job.status === "BLOCKED") && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 bg-slate-50 px-4 py-3">
          <div>
            <p className="text-[11px] font-semibold text-slate-800">{job.failure_detail ?? "The workflow needs attention."}</p>
            {recoveryGuidance && <p className="mt-1 text-[11px] leading-5 text-slate-600">{recoveryGuidance}</p>}
            <p className="mt-0.5 font-mono text-[9px] uppercase tracking-wider text-slate-500">
              {job.failure_code ?? "WORKFLOW_BLOCKED"}{activeOrFailed ? ` · ${activeOrFailed.label}` : ""} · attempt {job.attempt_count}/{job.max_attempts}
            </p>
          </div>
          {retryable && (
            <button type="button" onClick={onRetry} className="rounded-lg border border-slate-900 bg-white px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-slate-900 hover:bg-slate-100">
              Retry saved workflow · {job.recovery.remaining_attempts} left
            </button>
          )}
        </div>
      )}
    </section>
  );
}

export function ConnectDatasetModal({ open, onClose, onSyncSuccess }: ConnectDatasetModalProps) {
  const [sessionId, setSessionId] = useState("");
  const [keyId, setKeyId] = useState("");
  const [keySecret, setKeySecret] = useState("");
  const [periodStart, setPeriodStart] = useState(() => utcDateOffset(-30));
  const [periodEnd, setPeriodEnd] = useState(() => utcDateOffset(0));
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [demoGenerating, setDemoGenerating] = useState(false);
  // Server state whose correctness depends on ordering and identity lives in
  // one reducer, so a previous import cannot leak into a newer one and an
  // out-of-order response cannot overwrite a newer one.
  const [server, dispatch] = useReducer(importSessionReducer, INITIAL_IMPORT_SESSION_STATE);
  const requestRef = useRef(0);
  const [pending, setPending] = useState<PendingCsv | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [analyzing, setAnalyzing] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [workflow, workflowDispatch] = useReducer(
    reconciliationWorkflowReducer,
    INITIAL_RECONCILIATION_WORKFLOW_STATE,
  );
  // Held when a run completed safely but was NOT fully investigated.
  const [investigationReport, setInvestigationReport] = useState<RunInvestigationReport | null>(null);
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null);
  const [reconciliationMode, setReconciliationMode] = useState<ReconciliationMode>("rules-only");
  const [aiStatusLoading, setAiStatusLoading] = useState(false);
  const workflowRequestRef = useRef(0);
  const [fileError, setFileError] = useState<string | null>(null);
  const [intendedType, setIntendedType] = useState<DocumentType>("payments");
  const fileInput = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const mappingDialogRef = useRef<HTMLElement>(null);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (!open) return;
    const dialog = pending ? mappingDialogRef.current : dialogRef.current;
    if (!dialog) return;
    const previous = document.activeElement as HTMLElement | null;
    const focusable = () => Array.from(dialog.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]',
    )).filter((element) => element.getClientRects().length > 0);
    focusable()[0]?.focus();
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        if (pending) setPending(null);
        else onClose();
      }
      if (event.key !== "Tab") return;
      const elements = focusable();
      const first = elements[0], last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first?.focus();
      }
    }
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("keydown", handleKey);
      if (previous?.isConnected) previous.focus();
    };
  }, [open, pending, onClose]);

  useEffect(() => {
    const existing = window.sessionStorage.getItem(IMPORT_SESSION_KEY);
    const next = existing ?? createImportSessionId();
    window.sessionStorage.setItem(IMPORT_SESSION_KEY, next);
    setSessionId(next);
  }, []);

  const sessionStatus = server.sessionStatus as SessionStatus | null;

  // Load the link first, then its detail, under ONE epoch. Never fetch an ID
  // captured by a previous render. Close, reopen and mutations invalidate both.
  const refreshSession = useCallback(async () => {
    if (!sessionId) return;
    const requestId = ++requestRef.current;
    dispatch({ type: "REFRESH_STARTED", requestId });
    try {
      const response = await fetch(`/api/v1/ingest/sessions/${sessionId}/status`);
      const status = await response.json();
      if (!response.ok) throw new Error(status.detail || "Import session status could not be loaded.");
      if (requestRef.current !== requestId) return;
      let detail: GatewayImportDetail | null = null;
      if (status.gateway_import_id) {
        const detailResponse = await fetch(
          `/api/v1/razorpay/imports/${encodeURIComponent(status.gateway_import_id)}?session_id=${encodeURIComponent(sessionId)}`,
        );
        const result = await detailResponse.json();
        if (!detailResponse.ok) throw new Error(result.detail || "The linked import could not be restored.");
        detail = result as GatewayImportDetail;
        if (detail.import_id !== status.gateway_import_id) throw new Error("Import identity did not match the session.");
      }
      if (requestRef.current !== requestId) return;
      dispatch({ type: "REFRESH_LOADED", requestId, status: status as SessionStatus, detail });
    } catch (error) {
      if (requestRef.current !== requestId) return;
      dispatch({ type: "REFRESH_FAILED", requestId });
      setSyncError(error instanceof Error ? error.message : "Import state could not be loaded.");
    }
  }, [sessionId]);

  const invalidateRequest = useCallback(() => {
    dispatch({ type: "RESET", requestId: ++requestRef.current });
  }, []);

  const invalidateWorkflowRequest = useCallback(() => {
    workflowDispatch({ type: "RESET", requestId: ++workflowRequestRef.current });
  }, []);

  useEffect(() => {
    if (open) void refreshSession();
    return invalidateRequest;
  }, [open, refreshSession, invalidateRequest]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setAiStatusLoading(true);
    void fetch("/api/v1/ai/status")
      .then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error("AI status unavailable");
        return body as AiStatus;
      })
      .then((status) => {
        if (cancelled) return;
        setAiStatus(status);
        setReconciliationMode(
          status.live_available ? "agent" : status.fake_selected ? "fake" : "rules-only",
        );
      })
      .catch(() => {
        if (!cancelled) {
          setAiStatus(null);
          setReconciliationMode("rules-only");
        }
      })
      .finally(() => {
        if (!cancelled) setAiStatusLoading(false);
      });
    return () => { cancelled = true; };
  }, [open]);

  const finishSuccessfulJob = useCallback((job: ReconciliationJob) => {
    if (!job.run_id) throw new Error("The completed workflow did not link a reconciliation run.");
    window.sessionStorage.removeItem(reconciliationJobStorageKey(sessionId));
    const report = describeRunInvestigation(job.summary);
    onSyncSuccess(job.run_id, job.summary);
    if (report.warning) {
      // The financial run is safe and linked, but it is NOT fully
      // investigated. Hold the dialog open so that is read, not skipped past.
      setInvestigationReport(report);
      return;
    }
    onClose();
  }, [onClose, onSyncSuccess, sessionId]);

  const pollReconciliationJob = useCallback(async (
    jobId: string,
    requestId: number,
    workflowSessionId: string,
  ) => {
    while (workflowRequestRef.current === requestId) {
      const response = await fetch(`/api/v1/ingest/reconciliation-jobs/${encodeURIComponent(jobId)}`);
      const body = await response.json().catch(() => ({})) as Partial<ReconciliationJob> & { detail?: string };
      if (!response.ok) throw new Error(body.detail || "Reconciliation progress could not be loaded.");
      if (workflowRequestRef.current !== requestId) return;
      const job = requireReconciliationJob(body, workflowSessionId, jobId);
      workflowDispatch({
        type: "JOB_RECEIVED",
        requestId,
        sessionId: workflowSessionId,
        job,
      });
      if (job.status === "SUCCEEDED") {
        finishSuccessfulJob(job);
        return;
      }
      if (job.status === "FAILED" || job.status === "BLOCKED") {
        return;
      }
      await delay(700);
    }
  }, [finishSuccessfulJob]);

  useEffect(() => {
    if (!open || !sessionId) return;
    const jobId = window.sessionStorage.getItem(reconciliationJobStorageKey(sessionId));
    if (!jobId) return;
    const requestId = ++workflowRequestRef.current;
    workflowDispatch({ type: "STARTED", requestId, sessionId, preserveJob: true });
    void pollReconciliationJob(jobId, requestId, sessionId).catch((error) => {
      if (workflowRequestRef.current !== requestId) return;
      workflowDispatch({
        type: "STATUS_UNAVAILABLE",
        requestId,
        sessionId,
        message: error instanceof Error
          ? `${error.message} The job is still saved; checking again is safe.`
          : "Reconciliation status is temporarily unavailable. The job is still saved.",
      });
    });
    return invalidateWorkflowRequest;
  }, [open, sessionId, pollReconciliationJob, invalidateWorkflowRequest]);

  const workflowBusy = isWorkflowBusy(workflow);
  const workflowRequiresChange =
    workflow.job?.status === "FAILED" &&
    workflow.job.recovery.action === "REVIEW_INPUTS_OR_CONFIGURATION";
  const busy = syncing || demoGenerating || analyzing || committing || workflowBusy || server.refreshing || aiStatusLoading;
  const activeSources = sessionStatus?.active_sources ?? {};
  const gatewayReady = sessionStatus?.gateway_ready ?? false;
  const bankReady = sessionStatus?.bank_ready ?? false;
  const ledgerReady = sessionStatus?.ledger_ready ?? false;
  const readyCount = sessionStatus?.ready_source_groups ?? 0;
  const fullReady = (sessionStatus?.ready ?? false) && !busy;
  const stagedSources = Object.values(activeSources).filter(
    (source): source is ActiveSource => source !== undefined,
  );
  const currentImportId = activeImportId(server);
  const gatewayView = useMemo(
    () =>
      buildGatewayView(
        server.syncResult,
        server.detail,
        sessionStatus
          ? {
              gatewayImportId: sessionStatus.gateway_import_id,
              settlementReconciliationRequired: sessionStatus.settlement_reconciliation_required,
            }
          : null,
      ),
    [server.syncResult, server.detail, sessionStatus],
  );
  const demoView = useMemo(
    () => buildDemoView(server.freshDemo, server.detail, currentImportId),
    [server.freshDemo, server.detail, currentImportId],
  );
  const capturedPayments = capturedPaymentCount(gatewayView?.paymentCounts);

  const resetWorkflowForEvidenceChange = useCallback(() => {
    if (sessionId) window.sessionStorage.removeItem(reconciliationJobStorageKey(sessionId));
    invalidateWorkflowRequest();
    setInvestigationReport(null);
  }, [sessionId, invalidateWorkflowRequest]);

  function chooseFile(type: DocumentType) {
    if (busy) return;
    setIntendedType(type);
    setFileError(null);
    if (fileInput.current) {
      fileInput.current.value = "";
      fileInput.current.click();
    }
  }

  async function analyzeSelectedFile(file: File | undefined) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".csv")) {
      setFileError("CSV only for this cornerstone. Image, OCR, XLSX and PDF imports are disabled.");
      return;
    }
    setAnalyzing(true);
    setFileError(null);
    try {
      const content = await file.text();
      const response = await fetch("/api/v1/ingest/analyze-csv", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, content, file_type: intendedType }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "The CSV could not be analyzed.");
      const analysis = result as CsvAnalysis;
      setPending({ filename: file.name, content, fileType: intendedType, analysis });
      setMapping(Object.fromEntries(analysis.mappings.map((item) => [item.target_field, item.source_column])));
    } catch (error) {
      setFileError(error instanceof Error ? error.message : "The CSV could not be analyzed.");
    } finally {
      setAnalyzing(false);
    }
  }

  async function commitMapping() {
    if (!pending || busy) return;
    resetWorkflowForEvidenceChange();
    const requestId = ++requestRef.current;
    dispatch({ type: "MUTATION_STARTED", requestId });
    setCommitting(true);
    setFileError(null);
    try {
      const mappings = Object.entries(mapping).filter(([, source]) => source).map(([target_field, source_column]) => ({ target_field, source_column }));
      const response = await fetch("/api/v1/ingest/commit-csv", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: pending.filename, content: pending.content, file_type: pending.fileType, session_id: sessionId, mappings }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "The reviewed mapping could not be committed.");
      if (requestRef.current !== requestId) return;
      await refreshSession();
      setPending(null);
      setMapping({});
    } catch (error) {
      setFileError(error instanceof Error ? error.message : "The reviewed mapping could not be committed.");
    } finally {
      setCommitting(false);
    }
  }

  async function importRazorpay() {
    if (busy) return;
    resetWorkflowForEvidenceChange();
    const requestId = ++requestRef.current;
    setSyncing(true);
    setSyncError(null);
    // Invalidates every value scoped to the previous import, including any
    // demo badge and any detail response still in flight for it.
    dispatch({ type: "MUTATION_STARTED", requestId });
    try {
      const credentials = {
        key_id: keyId.trim() || undefined,
        key_secret: keySecret.trim() || undefined,
      };
      setKeyId("");
      setKeySecret("");
      const response = await fetch("/api/v1/razorpay/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...credentials, count: 1000, session_id: sessionId, period_start: periodStart, period_end: periodEnd, auto_reconcile: false }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Razorpay Test Mode import failed.");
      if (requestRef.current !== requestId) return;
      dispatch({ type: "SYNC_SUCCEEDED", requestId, result: result as RazorpaySyncResult });
      await refreshSession();
    } catch (error) {
      if (requestRef.current !== requestId) return;
      setSyncError(error instanceof Error ? error.message : "Razorpay Test Mode import failed.");
      await refreshSession();
    } finally {
      setSyncing(false);
    }
  }

  async function generateDemoEvidence() {
    const importId = currentImportId;
    if (!importId || !sessionId || busy || !server.detail?.demo_generation?.eligible) return;
    resetWorkflowForEvidenceChange();
    const requestId = ++requestRef.current;
    dispatch({ type: "MUTATION_STARTED", requestId });
    setDemoGenerating(true);
    setSyncError(null);
    try {
      // Never fall back to the legacy endpoint: an older running backend
      // implements that action as a five-source bundle, including merchant files.
      const response = await fetch(`/api/v1/razorpay/imports/${encodeURIComponent(importId)}/generate-gateway-evidence`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      });
      if (response.status === 404 || response.status === 405) {
        throw new Error("Gateway-only generation is unavailable on this backend. Restart the backend with the updated code; no legacy generation was attempted.");
      }
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Demo evidence could not be generated.");
      if (requestRef.current !== requestId) return;
      const evidence = result as DemoEvidenceResult;
      dispatch({
        type: "DEMO_SUCCEEDED",
        requestId,
        evidence: {
          importId,
          evidence_id: evidence.evidence_id,
          provenance: evidence.provenance,
          message: evidence.message,
        },
      });
      await refreshSession();
    } catch (error) {
      if (requestRef.current !== requestId) return;
      setSyncError(error instanceof Error ? error.message : "Demo evidence could not be generated.");
      await refreshSession();
    } finally {
      setDemoGenerating(false);
    }
  }

  async function runReconciliation() {
    if (!fullReady) return;
    setFileError(null);
    setInvestigationReport(null);
    const requestId = ++workflowRequestRef.current;
    workflowDispatch({ type: "STARTED", requestId, sessionId });
    try {
      const response = await fetch("/api/v1/ingest/reconciliation-jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, mode: reconciliationMode }),
      });
      const result = await response.json().catch(() => ({})) as Partial<ReconciliationJob> & { detail?: string };
      if (!response.ok) throw new Error(result.detail || "Full reconciliation could not start.");
      if (workflowRequestRef.current !== requestId) return;
      const job = requireReconciliationJob(result, sessionId);
      workflowDispatch({ type: "JOB_RECEIVED", requestId, sessionId, job });
      window.sessionStorage.setItem(reconciliationJobStorageKey(sessionId), job.job_id);
      if (job.status === "SUCCEEDED") {
        finishSuccessfulJob(job);
        return;
      }
      if (job.terminal) return;
      await pollReconciliationJob(job.job_id, requestId, sessionId);
    } catch (error) {
      if (workflowRequestRef.current !== requestId) return;
      workflowDispatch({
        type: "STATUS_UNAVAILABLE",
        requestId,
        sessionId,
        message: error instanceof Error ? error.message : "Full reconciliation could not start.",
      });
    }
  }

  async function retryReconciliation() {
    const failedJob = workflow.job;
    if (!canRetryWorkflow(failedJob)) return;
    setFileError(null);
    const requestId = ++workflowRequestRef.current;
    workflowDispatch({ type: "STARTED", requestId, sessionId, preserveJob: true });
    try {
      const response = await fetch(
        `/api/v1/ingest/reconciliation-jobs/${encodeURIComponent(failedJob!.job_id)}/retry`,
        { method: "POST" },
      );
      const body = await response.json().catch(() => ({})) as Partial<ReconciliationJob> & { detail?: string };
      if (!response.ok) throw new Error(body.detail || "The reconciliation job could not be retried.");
      if (workflowRequestRef.current !== requestId) return;
      const job = requireReconciliationJob(body, sessionId, failedJob!.job_id);
      workflowDispatch({ type: "JOB_RECEIVED", requestId, sessionId, job });
      await pollReconciliationJob(job.job_id, requestId, sessionId);
    } catch (error) {
      if (workflowRequestRef.current !== requestId) return;
      workflowDispatch({
        type: "STATUS_UNAVAILABLE",
        requestId,
        sessionId,
        message: error instanceof Error ? error.message : "The reconciliation job could not be retried.",
      });
    }
  }

  function resumeReconciliationStatus() {
    const jobId = workflow.job?.job_id;
    if (!jobId) {
      void runReconciliation();
      return;
    }
    const requestId = ++workflowRequestRef.current;
    workflowDispatch({ type: "STARTED", requestId, sessionId, preserveJob: true });
    void pollReconciliationJob(jobId, requestId, sessionId).catch((error) => {
      if (workflowRequestRef.current !== requestId) return;
      workflowDispatch({
        type: "STATUS_UNAVAILABLE",
        requestId,
        sessionId,
        message: error instanceof Error ? error.message : "Reconciliation status is temporarily unavailable.",
      });
    });
  }

  if (!open) return null;

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto p-3 sm:p-6">
        <motion.button
          type="button"
          aria-label="Close import dialog"
          className="fixed inset-0 bg-slate-950/45 backdrop-blur-[2px]"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        />
        <motion.section
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="import-title"
          initial={{ opacity: 0, y: 16, scale: 0.99 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 10, scale: 0.995 }}
          transition={{ duration: reducedMotion ? 0 : 0.2, ease: [0.22, 1, 0.36, 1] }}
          className="relative z-10 my-auto flex max-h-[92dvh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-[#f8fafc] text-slate-900 shadow-[0_32px_90px_rgba(15,23,42,0.22)] [&_button]:focus-visible:outline-2 [&_button]:focus-visible:outline-offset-2 [&_button]:focus-visible:outline-slate-900 [&_summary]:focus-visible:outline-2 [&_summary]:focus-visible:outline-slate-900"
        >
          <div className="shrink-0 border-b border-slate-200 bg-white px-4 py-4 sm:px-5">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.22em] text-slate-500">
                  Source intake / session {sessionId.slice(-6).toUpperCase()}
                </p>
                <h2 id="import-title" className="mt-1 text-lg font-bold tracking-tight text-slate-950">
                  Import evidence
                </h2>
                <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-500">
                  Connect Razorpay, then upload your bank statement and ledger.
                </p>
              </div>
              <button type="button" onClick={onClose} className="rounded-full border border-slate-200 bg-white p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-950" aria-label="Close">
                <IconX size={16} />
              </button>
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3">
              <div className="flex gap-1.5" aria-label="Source readiness">
                <StatusMark ready={gatewayReady} label="1 · Gateway" />
                <StatusMark ready={bankReady} label="2 · Bank" />
                <StatusMark ready={ledgerReady} label="3 · Ledger" />
              </div>
              <span role="status" className="font-mono text-[11px] font-semibold text-slate-600">{server.refreshing ? "Checking sources…" : `${readyCount}/3 sources ready`}</span>
            </div>
          </div>

          <input ref={fileInput} type="file" accept=".csv,text/csv" className="hidden" onChange={(event) => void analyzeSelectedFile(event.target.files?.[0])} />

          <div className="min-h-0 space-y-3 overflow-y-auto p-3 sm:p-5">
            <article className="rounded-xl border border-slate-200 bg-white p-3 sm:p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex gap-3">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-950 font-mono text-xs font-bold text-white">01</span>
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-[13px] font-bold text-slate-950">Razorpay Test Mode</h3>
                      <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-slate-600">Official APIs</span>
                    </div>
                    <p className="mt-0.5 text-[11px] text-slate-500">Official API data · Test credentials only</p>
                  </div>
                </div>
                <StatusMark ready={gatewayReady} label={gatewayReady ? "Gateway ready" : "Required"} />
              </div>
              <div className="mt-3 space-y-3">
                <details key={currentImportId ?? "new-import"} open={!gatewayView} className="group rounded-lg border border-slate-200 px-3 py-2">
                  <summary className="cursor-pointer text-xs font-semibold text-slate-700">{gatewayView ? "Import another period" : "Connect your Test Mode account"}</summary>
                <div className="mt-3 grid gap-3 sm:grid-cols-2 md:grid-cols-4">
                  {[
                    ["Test Key ID", keyId, setKeyId, "rzp_test_...", "text"],
                    ["Test Key Secret", keySecret, setKeySecret, "Never logged or returned", "password"],
                  ].map(([label, value, setter, placeholder, inputType]) => (
                    <label key={String(label)} className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">
                      {String(label)}
                      <input type={String(inputType)} value={String(value)} onChange={(event) => (setter as typeof setKeyId)(event.target.value)} placeholder={String(placeholder)} autoComplete={inputType === "password" ? "new-password" : "off"} className="mt-1.5 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 font-mono text-xs font-medium normal-case tracking-normal text-slate-900 outline-none transition-colors placeholder:text-slate-400 focus:border-slate-900 focus:bg-white" />
                    </label>
                  ))}
                  <label className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">Period start<input type="date" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} className="mt-1.5 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 font-mono text-xs font-medium normal-case tracking-normal text-slate-900 outline-none transition-colors focus:border-slate-900 focus:bg-white" /></label>
                  <label className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">Period end<input type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} className="mt-1.5 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 font-mono text-xs font-medium normal-case tracking-normal text-slate-900 outline-none transition-colors focus:border-slate-900 focus:bg-white" /></label>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <button type="button" onClick={() => void importRazorpay()} disabled={busy || !sessionId || !keyId.trim() || !keySecret.trim() || !periodStart || !periodEnd || periodStart > periodEnd} className="inline-flex items-center gap-2 rounded-lg bg-slate-950 px-3 py-2.5 text-xs font-semibold text-white transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500"><IconRazorpay size={14} />{syncing ? "Retrieving Razorpay data…" : "Connect and retrieve Razorpay data"}</button>
                  <span className="text-[11px] text-slate-500">Credentials are not saved.</span>
                </div>
                </details>
                {syncError && <ApiError message={syncError} />}
                {gatewayView && (
                  <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700">
                    <div className="flex flex-wrap items-center justify-between gap-2 pb-2.5">
                      <p className="break-all font-mono text-[10px] text-slate-500">Import {gatewayView.importId}</p>
                      <span className="rounded-full border border-slate-300 bg-white px-2 py-0.5 font-mono text-[9px] font-bold uppercase tracking-wider text-slate-600">{gatewayView.restored ? "Restored from backend" : "This request"}</span>
                    </div>
                    <div aria-label="Official Razorpay API counts" className="grid grid-cols-3 gap-3 border-t border-slate-200 pt-2.5 sm:grid-cols-5">
                      {[["Orders", gatewayView.ordersCount], ["Payments", gatewayView.paymentCounts.total], ["Refunds", gatewayView.refundCounts.total], ["Settlements", gatewayView.settlementsCount], ["Recon rows", gatewayView.reconciliationCount]].map(([label, count]) => (
                        <div key={String(label)}><p className="text-[9px] font-bold uppercase tracking-wider text-slate-400">{label}</p><p className="mt-0.5 font-mono text-base font-semibold text-slate-950">{count}</p></div>
                      ))}
                    </div>
                    <p className="mt-2 font-mono text-[9px] leading-4 uppercase tracking-wider text-slate-500">{gatewayView.paymentCounts.eligible} of {gatewayView.paymentCounts.total} payments reconciliation-eligible · {gatewayView.refundCounts.eligible} of {gatewayView.refundCounts.total} refunds eligible · official settlement rows {gatewayView.officialSettlementRowsReturned ? "returned" : "not returned"}</p>
                    <p className="mt-2 text-[11px] text-slate-500">Official counts only — synthetic evidence does not change these totals.</p>
                  </div>
                )}
                {gatewayView && capturedPayments === 0 && (
                  <div className="rounded-xl border border-slate-300 bg-white px-3 py-3 text-xs text-slate-900 shadow-2xs"><p className="font-bold">{gatewayView.paymentCounts.total === 0 ? "No payment records in this period" : `No captured payments among ${gatewayView.paymentCounts.total} payment records`}</p><p className="mt-1 text-[11px] leading-5 text-slate-500">{gatewayView.paymentCounts.total === 0 ? "Orders alone are not financial events. Complete Test Mode payments through Razorpay Checkout or choose a period containing captured payments." : `Every payment record in this import is uncaptured or otherwise ineligible (${gatewayView.paymentCounts.not_eligible} not eligible). Nothing here can be reconciled, and no demo evidence can be derived from it.`}</p></div>
                )}
                {gatewayView && capturedPayments > 0 && !gatewayView.workflowSettlementReady && (
                  <div className="overflow-hidden rounded-xl border border-slate-300 bg-white text-xs text-slate-900 shadow-2xs">
                    <div className="border-b border-slate-200 px-3 py-3 sm:px-4">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="font-bold">{gatewayView.paymentCounts.awaiting_settlement} eligible payment{gatewayView.paymentCounts.awaiting_settlement === 1 ? "" : "s"} awaiting settlement evidence</p>
                          <p className="mt-1 text-[11px] leading-5 text-slate-500">{gatewayView.readinessConfirmed ? "Add labelled gateway evidence to continue this demo." : "Session readiness for this import has not been confirmed yet."}</p>
                        </div>
                        <span className="rounded-full border border-slate-300 bg-slate-100 px-2 py-1 font-mono text-[9px] font-bold uppercase tracking-wider text-slate-700">Awaiting settlement</span>
                      </div>
                    </div>
                    <div className="bg-slate-50/70 px-3 py-3 sm:px-4">
                        <p className="text-[11px] leading-5 text-slate-600">Gateway only. Bank and ledger uploads stay separate and unchanged.</p>
                        {server.detail?.demo_generation?.reason && <p className="mt-2 text-[11px] text-slate-600">{server.detail.demo_generation.reason}</p>}
                        <button type="button" onClick={() => void generateDemoEvidence()} disabled={busy || !server.detail?.demo_generation?.eligible} className="mt-2.5 rounded-lg border border-slate-900 bg-slate-950 px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-white transition-colors hover:bg-slate-800 disabled:cursor-wait disabled:border-slate-300 disabled:bg-slate-300">
                          {demoGenerating ? "Generating gateway evidence…" : "Generate synthetic gateway evidence"}
                        </button>
                        <p className="mt-2 font-mono text-[10px] text-slate-600">SYNTHETIC_DEMO · Not Razorpay-issued evidence</p>
                    </div>
                  </div>
                )}
                {demoView && (
                  <div className="rounded-xl border border-slate-900 bg-slate-950 px-4 py-3 text-white">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-xs font-bold">{demoView.heading}</p>
                      <span className="rounded-full border border-white/30 px-2 py-1 font-mono text-[9px] font-bold uppercase tracking-wider">Not production eligible</span>
                    </div>
                    {demoView.activationState !== "ACTIVE" && <p className="mt-1.5 text-[11px] leading-5 text-slate-300">{demoView.message}</p>}
                    <p className="mt-2 break-all font-mono text-[10px] text-slate-300">Provenance {demoView.provenance} · {demoView.activationState}</p>
                    <p className="mt-1 text-[11px] text-slate-300">Bank and ledger readiness comes from your separate uploads.</p>
                  </div>
                )}
                {gatewayView && <details className="border-t border-slate-100 pt-2">
                  <summary className="cursor-pointer py-1 text-[11px] font-semibold text-slate-600">Import details & payment records</summary>
                  <div className="mt-2 space-y-3 text-[11px] leading-5 text-slate-500">
                    <p>{gatewayView.message}</p>
                    <p>{gatewayView.restored ? "Credentials were never persisted" : "Credentials discarded after this request"}</p>
                    <p className="font-semibold">Gateway intake dossier · all payment records</p>
                    <div className="space-y-1.5">{gatewayView.dossier.slice(0, 4).map((payment) => (
                      <div key={payment.payment_id} className="flex items-center justify-between gap-2 font-mono text-[11px]">
                        <span className="truncate">{payment.payment_id}</span>
                        <span className="ml-auto rounded border border-slate-200 px-1 text-[10px] uppercase">{payment.status || "unknown"}</span>
                        <span className="shrink-0 text-slate-900">{formatINR(payment.amount_paise)}</span>
                      </div>
                    ))}</div>
                    <p>{describeDossierPage(gatewayView, 4)}</p>
                    {demoView && <div className="border-t border-slate-200 pt-2">{demoView.activationState === "ACTIVE" && <p>{demoView.message}</p>}<p className="mt-1 break-all font-mono">Evidence {demoView.evidenceId}{demoView.restored ? " · restored from backend" : ""}</p></div>}
                  </div>
                </details>}
              </div>
            </article>

            {Boolean(sessionStatus?.merchant_upload_required?.length) && (
              <div className="rounded-xl border border-slate-300 bg-slate-50 px-4 py-3 text-xs leading-5 text-slate-700">
                <p className="font-bold text-slate-950">Separate merchant uploads required</p>
                <p>Legacy demo files do not count as merchant uploads. Replace {sessionStatus?.merchant_upload_required?.map((type) => LABELS[type]).join(" and ")} below; history is preserved.</p>
              </div>
            )}

            {(["bank_entries", "ledger_entries"] as DocumentType[]).map((type, index) => {
              const ready = type === "bank_entries" ? bankReady : ledgerReady;
              const source = activeSources[type];
              return <article key={type} className="rounded-xl border border-slate-200 bg-white p-3 sm:p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex min-w-0 flex-1 gap-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 font-mono text-xs font-bold">0{index + 2}</span>
                    <div className="min-w-0">
                      <h3 className="text-[13px] font-bold text-slate-950">{LABELS[type]}</h3>
                      <p className="mt-1 break-all text-[11px] text-slate-600">{source ? source.original_filename : "Upload a matching synthetic CSV"}</p>
                      {source && <p className="mt-1 text-[11px] text-slate-500">{source.origin === "MANUAL_CSV" ? "Your upload · saved in this session" : source.origin === "SYNTHETIC_DEMO" ? "Legacy auto-generated file · upload required" : source.origin} · {source.accepted_count} valid rows{source.quarantined_count > 0 ? ` · ${source.quarantined_count} quarantined` : ""}</p>}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusMark ready={ready} label={ready ? "Uploaded" : "Required"} />
                    <button type="button" onClick={() => chooseFile(type)} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"><IconUpload size={14} />{ready ? "Replace CSV" : "Choose CSV"}</button>
                  </div>
                </div>
              </article>;
            })}

            {(analyzing || fileError) && <div>{analyzing ? <p className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600">Profiling columns and checking known aliases…</p> : fileError && <ApiError message={fileError} />}</div>}

            <WorkflowProgress
              state={workflow}
              onRetry={() => void retryReconciliation()}
              onResume={resumeReconciliationStatus}
            />

            {stagedSources.length > 0 && <details className="px-1 py-1">
              <summary className="cursor-pointer text-[11px] font-semibold text-slate-600">Source revisions & validation · {stagedSources.length} active files</summary>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">{stagedSources.map((source) => <div key={source.source_type} className="rounded-lg border border-slate-200 bg-white p-3 text-[11px]"><p className="break-all font-semibold text-slate-900">{source.original_filename}</p><p className="mt-1 text-slate-500">{source.accepted_count}/{source.row_count} valid · {source.quarantined_count} quarantined</p><p className="mt-1 break-all text-slate-500">{LABELS[source.source_type]} · revision {source.revision_number} active · {source.origin} · SHA {source.canonical_sha256.slice(0, 10)}…</p></div>)}</div>
            </details>}
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-slate-200 bg-white px-4 py-3 sm:px-5">
            <p className="text-[11px] text-slate-500">{workflowBusy && workflow.job ? `${workflow.job.progress.headline} · ${workflow.job.provider_id}${workflow.job.simulated ? " · simulated" : ""}` : readyCount === 3 ? "Evidence collected. Reconciliation checks for differences." : "Three independent sources. One verified comparison."}</p>
            <div className="flex items-center gap-2">
              <label className="sr-only" htmlFor="reconciliation-mode">Reconciliation execution mode</label>
              <select id="reconciliation-mode" value={reconciliationMode} onChange={(event) => setReconciliationMode(event.target.value as ReconciliationMode)} disabled={workflowBusy || aiStatusLoading} className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-[11px] font-medium text-slate-600 outline-none focus:border-slate-900">
                {aiStatus?.live_available && <option value="agent">AI · {aiStatus.chain.join(" → ")}</option>}
                {aiStatus?.fake_selected && <option value="fake">Synthetic fake investigator</option>}
                <option value="rules-only">Rules only</option>
              </select>
              <button type="button" disabled={!fullReady || workflowBusy || canRetryWorkflow(workflow.job) || workflowRequiresChange} onClick={() => void runReconciliation()} className="rounded-lg bg-slate-950 px-4 py-2.5 text-xs font-semibold text-white transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500">{workflowBusy ? workflow.job?.status === "RUNNING" ? "Reconciliation in progress…" : "Starting saved workflow…" : canRetryWorkflow(workflow.job) ? "Retry saved workflow above" : workflowRequiresChange ? "Update evidence or configuration" : busy && readyCount === 3 ? "Checking sources…" : fullReady ? reconciliationMode === "agent" ? "Run with AI investigator" : reconciliationMode === "fake" ? "Run synthetic evaluation" : "Run rules-only reconciliation" : `Waiting for ${3 - readyCount} source${3 - readyCount === 1 ? "" : "s"}`}</button>
            </div>
            {investigationReport && (
              <div role="alert" className="mt-3 rounded-xl border border-slate-400 bg-white px-3 py-3 text-xs text-slate-900 shadow-2xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-bold">{investigationReport.label}</p>
                  <span className="rounded-full border border-slate-300 bg-slate-100 px-2 py-1 font-mono text-[9px] font-bold uppercase tracking-wider text-slate-700">Not fully investigated</span>
                </div>
                <p className="mt-1.5 text-[11px] leading-5 text-slate-600">{investigationReport.detail}</p>
                <p className="mt-2 font-mono text-[9px] uppercase tracking-wider text-slate-500">
                  attempted {investigationReport.attemptedProviders.length ? investigationReport.attemptedProviders.join(" + ") : "none"} · answered {investigationReport.actualProviders.length ? investigationReport.actualProviders.join(" + ") : "none"}
                </p>
                <button type="button" onClick={() => { setInvestigationReport(null); onClose(); }} className="mt-2.5 rounded-lg border border-slate-900 bg-slate-950 px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-white transition-colors hover:bg-slate-800">
                  Open the run anyway
                </button>
              </div>
            )}
          </div>
        </motion.section>

        {pending && <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/50 p-3 backdrop-blur-[2px]"><motion.section ref={mappingDialogRef} role="dialog" aria-modal="true" aria-label="Review CSV mapping" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{duration: reducedMotion ? 0 : 0.2}} className="max-h-[92vh] w-full max-w-4xl overflow-y-auto rounded-2xl border border-slate-200 bg-white p-5 text-slate-900 shadow-[0_28px_80px_rgba(15,23,42,0.24)] sm:p-6">
          <div className="flex items-start justify-between gap-4"><div><p className="font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">Schema review · {pending.analysis.mapping_provider.replace("_", " ")}</p><h3 className="mt-1 text-lg font-bold text-slate-950">Map {pending.filename}</h3><p className="mt-1 text-xs text-slate-500">Groq suggestions are proposals. Review every required field before deterministic validation.</p></div><button type="button" onClick={() => setPending(null)} className="rounded-full border border-slate-200 p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-950"><IconX size={15} /></button></div>
          <div className="mt-4 overflow-hidden rounded-xl border border-slate-200"><div className="grid grid-cols-[1fr_1fr_auto] gap-3 bg-slate-50 px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-slate-500"><span>ARGUS field</span><span>Uploaded column</span><span>Origin</span></div><div className="max-h-[48vh] divide-y divide-slate-100 overflow-y-auto">{TARGET_FIELDS[pending.fileType].map((target) => {
            const decision = pending.analysis.mappings.find((item) => item.target_field === target);
            const required = pending.analysis.required_fields.includes(target);
            return <div key={target} className="grid grid-cols-[1fr_1fr_auto] items-center gap-3 px-3 py-2.5 text-xs"><div><span className="font-mono font-medium text-slate-700">{target}</span>{required && <span className="ml-1 text-slate-950">*</span>}</div><select value={mapping[target] ?? ""} onChange={(event) => setMapping((current) => ({ ...current, [target]: event.target.value }))} className="min-w-0 rounded-lg border border-slate-200 bg-slate-50 px-2 py-2 text-xs text-slate-900 outline-none transition-colors focus:border-slate-900 focus:bg-white"><option value="">Not mapped</option>{pending.analysis.headers.map((header) => <option key={header} value={header}>{header}</option>)}</select><span className={`w-14 rounded border px-1.5 py-1 text-center font-mono text-[9px] ${decision ? "border-slate-300 bg-slate-100 text-slate-700" : "border-slate-200 bg-white text-slate-400"}`}>{decision?.origin ?? "MANUAL"}</span></div>;
          })}</div></div>
          {(pending.analysis.warnings.length > 0 || fileError) && <div className="mt-3 space-y-2">{pending.analysis.warnings.map((warning) => <p key={warning} className="rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-[11px] text-slate-700">{warning}</p>)}{fileError && <ApiError message={fileError} />}</div>}
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4"><p className="text-[11px] text-slate-500">{pending.analysis.row_count} source rows · SHA {pending.analysis.source_sha256.slice(0, 12)}… · Cell values will not be rewritten by AI. Confirmation preserves this revision and makes it active.</p><div className="flex gap-2"><button type="button" onClick={() => setPending(null)} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-600 transition-colors hover:bg-slate-50 hover:text-slate-950">Cancel</button><button type="button" disabled={committing} onClick={() => void commitMapping()} className="inline-flex items-center gap-1.5 rounded-lg bg-slate-950 px-4 py-2 text-xs font-bold text-white transition-colors hover:bg-slate-800 disabled:bg-slate-300 disabled:text-slate-500"><IconCheck size={14} />{committing ? "Validating every row…" : "Activate revision & validate"}</button></div></div>
        </motion.section></div>}
      </div>
    </AnimatePresence>
  );
}
