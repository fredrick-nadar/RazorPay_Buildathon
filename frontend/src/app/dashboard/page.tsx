"use client";

/**
 * ARGUS CONTROL control room.
 *
 * Every view here resolves ONE selection — the same run, the same case, the
 * same audit scope — from `lib/argus-selection`, which owns URL restoration,
 * request generations and response contract validation. The page renders
 * backend results only: no financial truth logic lives here and no metric is
 * displayed unless the API produced it. Loading, legitimate empty, backend
 * unavailable, not-found and partial states are first-class (PRD §13.4).
 */

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import Link from "next/link";
import type { AuditLogItem, CaseDetail, CaseSummary } from "../../lib/types";
import { formatCount, formatINR, formatSignedINR, formatUtc, shortHash } from "../../lib/format";
import { telemetryFromRun } from "../../lib/run-telemetry";
import {
  type ArgusView,
  classifyFailure,
  initialSelectionState,
  parseSelectionRequest,
  requireAuditEvents,
  requireCaseDetail,
  requireCaseList,
  requireRunView,
  selectionReducer,
  selectionSearchParams,
  selectionForUrl,
  DEFAULT_SELECTION_REQUEST,
} from "../../lib/argus-selection";
import { ApiStatusPanel } from "../../components/api-status-panel";
import { CaseRail, StatusBadge } from "../../components/case-rail";
import { CaseWorkspace } from "../../components/case-workspace";
import { EvidenceChain } from "../../components/evidence-chain";
import { AuditLog } from "../../components/audit-log";
import { ApprovalModal, type AuthorityDecision } from "../../components/approval-modal";
import { ConnectDatasetModal } from "../../components/connect-dataset-modal";
import { ExecutiveDossierModal } from "../../components/executive-dossier-modal";
import { FeeAuditCard } from "../../components/fee-audit-card";
import { HomeChat } from "../../components/home-chat";
import { MasterMatrixTable } from "../../components/master-matrix-table";
import {
  IconActivity,
  IconCheck,
  IconChevronDown,
  IconFlag,
  IconHome,
  IconLayers,
  IconPlug,
  IconPresentation,
  IconRoute,
  IconScale,
  IconScroll,
  IconShield,
  IconSidebar,
  IconTrendingUp,
} from "../../components/icons";
import { Metric, Toast, type ToastState } from "../../components/primitives";
import { CaseStatus } from "../../domain/enums";

type Tab = ArgusView;

/** Human-readable reasons for a fail-closed selection, by safe backend code. */
const FAILURE_COPY: Record<string, string> = {
  RUN_OR_CASE_NOT_FOUND: "The selected run or case no longer exists.",
  SELECTION_CONFLICT: "That case does not belong to the selected run.",
  BACKEND_UNREACHABLE: "The backend did not answer.",
  RUN_CONTRACT_INVALID: "The run response did not match the expected contract.",
  RUN_IDENTITY_MISMATCH: "The response described a different run than the selected one.",
  CASES_RUN_MISMATCH: "The case list did not belong to the selected run.",
  CASE_IDENTITY_MISMATCH: "The response described a different case than the selected one.",
  CASE_RUN_MISMATCH: "That case does not belong to the selected run.",
  AUDIT_ORDER_INVALID: "The audit trail did not arrive in its authoritative append order.",
  AUDIT_SCOPE_MISMATCH: "The audit trail contained events outside the selected scope.",
};

function failureCopy(code: string | null): string {
  if (!code) return "The view could not be loaded.";
  return FAILURE_COPY[code] ?? `The view could not be loaded (${code}).`;
}

/** Read one JSON body, turning a non-OK response into a classified failure. */
async function readJson(url: string): Promise<unknown> {
  const response = await fetch(url);
  if (!response.ok) {
    const error = new Error(`HTTP ${response.status}`) as Error & { httpStatus: number };
    error.httpStatus = response.status;
    throw error;
  }
  return (await response.json()) as unknown;
}

function httpStatusOf(error: unknown): number | undefined {
  return typeof error === "object" && error !== null && "httpStatus" in error
    ? (error as { httpStatus?: number }).httpStatus
    : undefined;
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function ControlRoomPage() {
  const [selection, dispatch] = useReducer(
    selectionReducer,
    DEFAULT_SELECTION_REQUEST,
    initialSelectionState,
  );

  const [statusFilter, setStatusFilter] = useState("ALL");
  const [categoryFilter, setCategoryFilter] = useState("ALL");
  const [searchQuery, setSearchQuery] = useState("");

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [buildSectionOpen, setBuildSectionOpen] = useState(true);
  const [investigationSectionOpen, setInvestigationSectionOpen] = useState(true);

  const [actionBusy, setActionBusy] = useState(false);
  const [modalAction, setModalAction] = useState<"APPROVE" | "REJECT">("APPROVE");
  const [modalOpen, setModalOpen] = useState(false);
  const [connectDatasetOpen, setConnectDatasetOpen] = useState(false);
  const [dossierModalOpen, setDossierModalOpen] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);

  const runGeneration = useRef(0);
  const caseGeneration = useRef(0);

  const activeTab = selection.request.view;
  const activeRun = selection.run;
  const activeRunId = activeRun?.run_id ?? null;
  const cases = selection.cases;
  const caseDetail = selection.caseDetail;
  const selectedCaseId = selection.caseId;
  const booting = selection.status === "LOADING";
  const backendReachable =
    selection.status === "READY" || selection.status === "EMPTY"
      ? true
      : selection.status === "UNAVAILABLE" || selection.status === "NOT_FOUND"
        ? false
        : null;

  useEffect(() => {
    window.dispatchEvent(new CustomEvent("argus-dashboard-tab", { detail: { tab: activeTab } }));
  }, [activeTab]);

  /* ----------------------------- fetching ------------------------- */

  const selectCase = useCallback(async (runId: string, caseId: string) => {
    const requestId = ++caseGeneration.current;
    dispatch({ type: "CASE_REQUESTED", requestId, runId, caseId });
    const scope = `?run_id=${encodeURIComponent(runId)}`;
    try {
      const [detailBody, auditBody] = await Promise.all([
        readJson(`/api/v1/cases/${encodeURIComponent(caseId)}${scope}`),
        readJson(`/api/v1/cases/${encodeURIComponent(caseId)}/audit${scope}`),
      ]);
      // Validate identity BEFORE the reducer is allowed to render either.
      const detail: CaseDetail = requireCaseDetail(detailBody, runId, caseId);
      const audit: AuditLogItem[] = requireAuditEvents(auditBody, { runId, caseId });
      dispatch({ type: "CASE_LOADED", requestId, runId, caseId, detail, audit });
    } catch (error) {
      const { status, code } = classifyFailure(error, httpStatusOf(error));
      dispatch({ type: "CASE_FAILED", requestId, runId, caseId, status, code });
    }
  }, []);

  const loadRun = useCallback(
    async (runId: string | null, preferredCaseId: string | null) => {
      const requestId = ++runGeneration.current;
      // A new run request invalidates in-flight case responses too.
      caseGeneration.current += 1;
      dispatch({ type: "RUN_REQUESTED", requestId, runId, caseId: preferredCaseId });

      try {
        const runBody = await readJson(
          runId
            ? `/api/v1/runs/${encodeURIComponent(runId)}/summary`
            : "/api/v1/runs/active",
        );
        if (runBody === null) {
          dispatch({ type: "RUN_EMPTY", requestId });
          return;
        }
        const run = requireRunView(runBody, runId);
        const [casesBody, auditBody] = await Promise.all([
          readJson(`/api/v1/runs/${encodeURIComponent(run.run_id)}/cases`),
          readJson(`/api/v1/runs/${encodeURIComponent(run.run_id)}/audit`),
        ]);
        const runCases: CaseSummary[] = requireCaseList(casesBody, run.run_id);
        const runAudit: AuditLogItem[] = requireAuditEvents(auditBody, { runId: run.run_id });

        if (requestId !== runGeneration.current) return;
        dispatch({ type: "RUN_LOADED", requestId, run, cases: runCases, runAudit });

        // Restore the requested case when it is still valid for this run,
        // otherwise open the first one. A stale case id is never forced.
        const restorable =
          preferredCaseId && runCases.some((item) => item.case_id === preferredCaseId)
            ? preferredCaseId
            : runCases[0]?.case_id;
        if (restorable) {
          void selectCase(run.run_id, restorable);
        } else {
          dispatch({ type: "CASE_CLEARED", requestId: ++caseGeneration.current });
        }
      } catch (error) {
        const { status, code } = classifyFailure(error, httpStatusOf(error));
        dispatch({ type: "RUN_FAILED", requestId, status, code });
      }
    },
    [selectCase],
  );

  // Restore the selection the URL asks for, once, on mount.
  const restored = useRef(false);
  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    const request = parseSelectionRequest(window.location.search);
    dispatch({ type: "VIEW_CHANGED", view: request.view });
    void loadRun(request.runId, request.caseId);
  }, [loadRun]);

  // Keep the URL in step so a refresh or a reopened link restores this context.
  useEffect(() => {
    if (!restored.current) return;
    const next = `${window.location.pathname}${selectionSearchParams(selectionForUrl(selection))}`;
    if (`${window.location.pathname}${window.location.search}` !== next) {
      window.history.replaceState(null, "", next);
    }
  }, [selection]);

  const setActiveTab = useCallback((view: Tab) => {
    dispatch({ type: "VIEW_CHANGED", view });
  }, []);

  const openCase = useCallback(
    (caseId: string) => {
      if (!activeRunId) return;
      void selectCase(activeRunId, caseId);
    },
    [activeRunId, selectCase],
  );

  const retryDashboardLoad = useCallback(() => {
    void loadRun(selection.request.runId, selection.request.caseId);
  }, [loadRun, selection.request.runId, selection.request.caseId]);

  const retryCaseLoad = useCallback(() => {
    if (activeRunId && selectedCaseId) void selectCase(activeRunId, selectedCaseId);
  }, [activeRunId, selectedCaseId, selectCase]);

  /* ----------------------------- authority ------------------------ */

  async function confirmAuthority(decision: AuthorityDecision) {
    if (!caseDetail || !activeRunId) return;
    const currentCaseId = caseDetail.case.case_id;
    // The decision is bound to the proof and the run that were on screen. The
    // backend refuses it with 409 if either has moved on.
    setActionBusy(true);
    try {
      const path = modalAction === "APPROVE" ? "approve" : "reject";
      const response = await fetch(
        `/api/v1/cases/${encodeURIComponent(currentCaseId)}/${path}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            proof_id: decision.proofId,
            run_id: activeRunId,
            reviewer_id: decision.reviewerId,
            notes: decision.notes || `${modalAction} authorized via dashboard`,
          }),
        },
      );
      if (response.status === 409) {
        setModalOpen(false);
        setToast({
          kind: "error",
          message:
            "This proposal was superseded before authorization. Nothing was applied; reloading the case.",
        });
        await loadRun(activeRunId, currentCaseId);
        return;
      }
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Authority submission failed");
      }
      const result = (await response.json()) as { reused?: boolean };
      setToast({
        kind: "success",
        message:
          modalAction === "APPROVE"
            ? result.reused
              ? `Case ${currentCaseId} was already applied; the existing simulated entry stands.`
              : `Case ${currentCaseId} approved. One simulated correction entry was created.`
            : `Case ${currentCaseId} rejected. No ledger entry was created.`,
      });
      setModalOpen(false);
      await loadRun(activeRunId, currentCaseId);
    } catch (error) {
      setToast({
        kind: "error",
        message: error instanceof Error ? error.message : "Authority submission failed",
      });
    } finally {
      setActionBusy(false);
    }
  }

  /* ----------------------------- derived -------------------------- */

  const telemetry = activeRun ? telemetryFromRun(activeRun) : undefined;

  const approvalCases = useMemo(
    () => cases.filter((item) => item.status === CaseStatus.APPROVAL_REQUIRED),
    [cases],
  );
  const verifiedCases = useMemo(
    () =>
      cases.filter(
        (item) =>
          item.status === CaseStatus.VERIFIED_RESOLVED ||
          item.status === CaseStatus.SIMULATED_APPLIED,
      ),
    [cases],
  );
  const unresolvedCases = useMemo(
    () => cases.filter((item) => item.status === CaseStatus.UNRESOLVED),
    [cases],
  );

  /** Shared banner for a case pane that failed closed or is still loading. */
  const casePaneNotice =
    selection.caseStatus === "LOADING" ? (
      <CasePane title="Loading case dossier…" detail="Reading the persisted case evidence." />
    ) : selection.caseStatus === "UNAVAILABLE" || selection.caseStatus === "NOT_FOUND" ? (
      <CasePane
        title="This case could not be opened"
        detail={`${failureCopy(selection.caseErrorCode)} The previous case is not shown in its place.`}
        action={
          <button
            type="button"
            onClick={retryCaseLoad}
            className="mt-3 rounded-lg border border-slate-900 bg-white px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-slate-900 hover:bg-slate-50"
          >
            Retry case
          </button>
        }
      />
    ) : null;

  return (
    <div className="flex h-screen overflow-hidden bg-[#f8fafc] text-slate-900 antialiased font-sans" suppressHydrationWarning>
      {/* ============================ Sarvam API Style Clean Sidebar ============================ */}
      <aside
        className={`group/sidebar flex shrink-0 flex-col border-r border-slate-200 bg-white transition-[width] duration-200 ease-in-out z-30 overflow-hidden select-none ${sidebarOpen ? "w-[240px]" : "w-[56px]"
          }`}
      >
        {/* Sidebar Header */}
        <div className="flex h-14 items-center px-2 border-b border-slate-100 shrink-0 overflow-hidden">
          {sidebarOpen ? (
            <div className="flex w-full items-center justify-between overflow-hidden">
              <Link
                href="/"
                aria-label="Back to home"
                className="flex items-center overflow-hidden transition hover:opacity-80 min-w-0"
              >
                <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                  <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-slate-900 text-white shadow-xs">
                    <svg viewBox="0 0 42 34" className="w-4 h-3.5" fill="currentColor" aria-hidden="true">
                      <polygon points="12,0 30,0 33.2,3.2 15.2,3.2" />
                      <polygon points="14.6,5.6 32.6,5.6 35.8,8.8 17.8,8.8" />
                      <polygon points="17.2,11.2 35.2,11.2 38.4,14.4 20.4,14.4" />
                      <polygon points="3.2,16.8 21.2,16.8 24.4,20 6.4,20" />
                      <polygon points="5.8,22.4 23.8,22.4 27,25.6 9,25.6" />
                      <polygon points="8.4,28 26.4,28 29.6,31.2 11.6,31.2" />
                    </svg>
                  </div>
                </div>
              </Link>

              <button
                onClick={() => setSidebarOpen(false)}
                aria-label="Collapse sidebar"
                title="Collapse sidebar"
                className="group flex h-9 w-9 shrink-0 items-center justify-center rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition-colors"
              >
                <IconSidebar size={18} className="text-slate-500 transition-transform duration-200 group-hover:scale-110 group-hover:text-slate-900" />
              </button>
            </div>
          ) : (
            <button
              onClick={() => setSidebarOpen(true)}
              aria-label="Expand sidebar"
              title="Expand sidebar"
              className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl hover:bg-slate-100 transition-all cursor-pointer"
            >
              {/* Argus Logo (shown when entire shrunk menu is not hovered) */}
              <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-slate-900 text-white shadow-xs transition-all duration-200 group-hover/sidebar:opacity-0 group-hover/sidebar:scale-75">
                <svg viewBox="0 0 42 34" className="w-4 h-3.5" fill="currentColor" aria-hidden="true">
                  <polygon points="12,0 30,0 33.2,3.2 15.2,3.2" />
                  <polygon points="14.6,5.6 32.6,5.6 35.8,8.8 17.8,8.8" />
                  <polygon points="17.2,11.2 35.2,11.2 38.4,14.4 20.4,14.4" />
                  <polygon points="3.2,16.8 21.2,16.8 24.4,20 6.4,20" />
                  <polygon points="5.8,22.4 23.8,22.4 27,25.6 9,25.6" />
                  <polygon points="8.4,28 26.4,28 29.6,31.2 11.6,31.2" />
                </svg>
              </div>

              {/* Sidebar Expand Icon (revealed when the entire shrunk menu is hovered) */}
              <div className="absolute inset-0 flex items-center justify-center opacity-0 scale-75 transition-all duration-200 group-hover/sidebar:opacity-100 group-hover/sidebar:scale-100 text-slate-600">
                <IconSidebar size={18} className="text-slate-600 group-hover:text-slate-900" />
              </div>
            </button>
          )}
        </div>

        {/* Sidebar Nav Items */}
        <div className="flex-1 space-y-4 overflow-y-auto overflow-x-hidden p-2">
          {/* Home Active Pill */}
          <div>
            <button
              onClick={() => {
                setStatusFilter("ALL");
                setCategoryFilter("ALL");
                setSearchQuery("");
                setActiveTab("home");
              }}
              title="Home"
              className={`group flex h-10 w-full items-center rounded-xl transition-all duration-150 overflow-hidden ${activeTab === "home"
                  ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                  : "text-slate-700 hover:bg-slate-50 hover:text-slate-900"
                }`}
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                <IconHome size={18} />
              </div>
              <div
                className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                  }`}
              >
                <span className="truncate text-[13px]">Home</span>
              </div>
            </button>
          </div>

          {/* Section: Build / Reconciliation */}
          <div className="space-y-1 overflow-hidden">
            {sidebarOpen ? (
              <button
                onClick={() => setBuildSectionOpen((o) => !o)}
                className="flex w-full items-center justify-between px-3 py-1 text-[11px] font-bold uppercase tracking-wider text-slate-400 hover:text-slate-700 transition-colors"
              >
                <span>Build</span>
                <IconChevronDown
                  size={13}
                  className={`transition-transform duration-300 ease-in-out ${buildSectionOpen ? "rotate-180" : "rotate-0"
                    }`}
                />
              </button>
            ) : (
              <div className="my-2 border-t border-slate-100 mx-1" />
            )}

            <div
              className={`grid transition-[grid-template-rows,opacity] duration-300 ease-in-out ${buildSectionOpen || !sidebarOpen
                  ? "grid-rows-[1fr] opacity-100"
                  : "grid-rows-[0fr] opacity-0 pointer-events-none"
                }`}
            >
              <div className="min-h-0 space-y-0.5 overflow-hidden">
                <button
                  onClick={() => setConnectDatasetOpen(true)}
                  disabled={booting}
                  title="Import Data (Live Razorpay API or Upload Files)"
                  className="group flex h-10 w-full items-center rounded-xl text-slate-700 hover:bg-slate-100/80 hover:text-slate-900 transition-all disabled:opacity-50 overflow-hidden"
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center text-slate-900">
                    <IconPlug size={17} />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px] font-medium">Import Data</span>
                  </div>
                </button>

                <button
                  onClick={() => {
                    setActiveTab("matrix");
                  }}
                  title="5-Way Reconciled Master Matrix"
                  className={`group flex h-10 w-full items-center rounded-xl transition-all overflow-hidden ${activeTab === "matrix"
                      ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                      : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
                    }`}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconLayers size={17} />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px]">5-Way Master Matrix</span>
                  </div>
                </button>

                <button
                  onClick={() => {
                    setCategoryFilter("ALL");
                    setSearchQuery("");
                    setStatusFilter(CaseStatus.APPROVAL_REQUIRED);
                    setActiveTab("approval_queue");
                    const match = cases.find((c) => c.status === CaseStatus.APPROVAL_REQUIRED);
                    if (match) openCase(match.case_id);
                  }}
                  title="Approval Queue"
                  className={`group flex h-10 w-full items-center rounded-xl transition-all overflow-hidden ${activeTab === "approval_queue"
                      ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                      : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
                    }`}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconShield size={17} />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px]">Approval Queue</span>
                  </div>
                </button>

                <button
                  onClick={() => {
                    setCategoryFilter("ALL");
                    setSearchQuery("");
                    setStatusFilter("ALL");
                    setActiveTab("verified_resolved");
                    const match = cases.find(
                      (c) =>
                        c.status === CaseStatus.VERIFIED_RESOLVED ||
                        c.status === CaseStatus.SIMULATED_APPLIED,
                    );
                    if (match) openCase(match.case_id);
                  }}
                  title="Verified Resolved & Applied"
                  className={`group flex h-10 w-full items-center rounded-xl transition-all overflow-hidden ${activeTab === "verified_resolved"
                      ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                      : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
                    }`}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconCheck size={17} />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px]">Verified Resolved</span>
                  </div>
                </button>

                <button
                  onClick={() => {
                    setCategoryFilter("ALL");
                    setSearchQuery("");
                    setStatusFilter(CaseStatus.UNRESOLVED);
                    setActiveTab("unresolved");
                    const match = cases.find((c) => c.status === CaseStatus.UNRESOLVED);
                    if (match) openCase(match.case_id);
                  }}
                  title="Unresolved Cases"
                  className={`group flex h-10 w-full items-center rounded-xl transition-all overflow-hidden ${activeTab === "unresolved"
                      ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                      : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
                    }`}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconFlag size={17} />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px]">Unresolved Cases</span>
                  </div>
                </button>
              </div>
            </div>
          </div>

          {/* Section: Investigation & Tools */}
          <div className="space-y-1 overflow-hidden">
            {sidebarOpen ? (
              <button
                onClick={() => setInvestigationSectionOpen((o) => !o)}
                className="flex w-full items-center justify-between px-3 py-1 text-[11px] font-bold uppercase tracking-wider text-slate-400 hover:text-slate-700 transition-colors"
              >
                <span>Investigation</span>
                <IconChevronDown
                  size={13}
                  className={`transition-transform duration-300 ease-in-out ${investigationSectionOpen ? "rotate-180" : "rotate-0"
                    }`}
                />
              </button>
            ) : (
              <div className="my-2 border-t border-slate-100 mx-1" />
            )}

            <div
              className={`grid transition-[grid-template-rows,opacity] duration-300 ease-in-out ${investigationSectionOpen || !sidebarOpen
                  ? "grid-rows-[1fr] opacity-100"
                  : "grid-rows-[0fr] opacity-0 pointer-events-none"
                }`}
            >
              <div className="min-h-0 space-y-0.5 overflow-hidden">
                <button
                  onClick={() => {
                    setStatusFilter("ALL");
                    setActiveTab("dossier");
                  }}
                  title="Case Dossier"
                  className={`group flex h-10 w-full items-center rounded-xl transition-all overflow-hidden ${activeTab === "dossier"
                      ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                      : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
                    }`}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconLayers size={17} />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px]">Case Dossier</span>
                  </div>
                </button>

                <button
                  onClick={() => setActiveTab("evidence")}
                  title="Evidence Trace"
                  className={`group flex h-10 w-full items-center rounded-xl transition-all overflow-hidden ${activeTab === "evidence"
                      ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                      : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
                    }`}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconRoute size={17} />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px]">Evidence Trace</span>
                  </div>
                </button>

                <button
                  onClick={() => setActiveTab("ledger")}
                  title="Ledger Dry-Run"
                  className={`group flex h-10 w-full items-center rounded-xl transition-all overflow-hidden ${activeTab === "ledger"
                      ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                      : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
                    }`}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconScale size={17} />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px]">Ledger Dry-Run</span>
                  </div>
                </button>

                <button
                  onClick={() => setActiveTab("audit")}
                  title="Audit Trail"
                  className={`group flex h-10 w-full items-center rounded-xl transition-all overflow-hidden ${activeTab === "audit"
                      ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                      : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
                    }`}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconScroll size={17} />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px]">Audit Trail</span>
                  </div>
                </button>

                <button
                  onClick={() => setActiveTab("fee_audit")}
                  title="MDR & GST Pricing Audit"
                  className={`group flex h-10 w-full items-center rounded-xl transition-all overflow-hidden ${activeTab === "fee_audit"
                      ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                      : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
                    }`}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconTrendingUp size={17} />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px]">MDR & GST Audit</span>
                  </div>
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Sidebar Footer Resources */}
        <div className="border-t border-slate-100 p-2 space-y-1 bg-slate-50/50 shrink-0 overflow-x-hidden">
          {/* A real destination. This was a non-interactive div with a
              permanently green dot that called no endpoint at all. */}
          <button
            type="button"
            onClick={() => setActiveTab("api_status")}
            title="API & integration status"
            className={`group flex h-10 w-full items-center rounded-xl overflow-hidden transition-all ${activeTab === "api_status"
                ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                : "text-slate-700 hover:bg-slate-100 hover:text-slate-900"
              }`}
          >
            <div className="flex h-10 w-10 shrink-0 items-center justify-center">
              <IconActivity size={17} />
            </div>
            <div
              className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                }`}
            >
              <span className="truncate text-[13px] font-medium">API Status</span>
            </div>
          </button>

          <Link
            href={`/presentation${activeRunId ? `?run=${encodeURIComponent(activeRunId)}` : ""}`}
            title="Presentation Mode"
            className="group flex h-10 w-full items-center rounded-xl text-slate-700 hover:bg-slate-100 hover:text-slate-900 transition-all overflow-hidden"
          >
            <div className="flex h-10 w-10 shrink-0 items-center justify-center">
              <IconPresentation size={17} />
            </div>
            <div
              className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                }`}
            >
              <span className="truncate text-[13px] font-medium">Presentation</span>
            </div>
          </Link>
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* Minimalist Top Header */}
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white/95 px-6 backdrop-blur-md">
          <div className="flex min-w-0 items-center gap-3">
            <h1 className="shrink-0 text-sm font-semibold tracking-tight text-slate-900">
              Argus Control <span className="hidden font-normal text-slate-400 lg:inline">· Financial Flight Recorder</span>
            </h1>
            {/* One selection identity, visible from every view. */}
            {activeRunId && (
              <span
                data-testid="active-run-identity"
                className="hidden min-w-0 items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 md:inline-flex"
                title={activeRunId}
              >
                <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Run</span>
                <span className="select-all truncate font-mono text-[11px] font-semibold text-slate-800">{activeRunId}</span>
              </span>
            )}
          </div>

          <div className="flex items-center gap-2.5">
            <span className="hidden md:inline-flex rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-800">
              Synthetic data only
            </span>
            <button
              onClick={() => setDossierModalOpen(true)}
              disabled={!activeRunId}
              className="hidden sm:inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-800 hover:bg-slate-50 hover:border-slate-300 transition-colors shadow-2xs cursor-pointer disabled:cursor-not-allowed disabled:opacity-40"
              title="Open evidence dossier for the selected run"
            >
              <IconShield size={13} className="text-slate-900" />
              <span>Evidence Dossier</span>
            </button>

            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${backendReachable === true
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                  : backendReachable === false
                    ? "border-rose-200 bg-rose-50 text-rose-800"
                    : "border-slate-200 bg-slate-50 text-slate-600"
                }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${backendReachable === true
                    ? "bg-emerald-500 animate-pulse-dot"
                    : backendReachable === false
                      ? "bg-rose-500"
                      : "bg-slate-400"
                  }`}
              />
              {backendReachable === true
                ? "Backend reachable"
                : backendReachable === false
                  ? "Backend unavailable"
                  : "Checking backend…"}
            </span>
          </div>
        </header>

        {/* The selected run no longer exists: fail closed with a way back. */}
        {selection.status === "NOT_FOUND" && (
          <div role="alert" className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-slate-300 bg-slate-100 px-4 py-3 sm:px-6">
            <div>
              <p className="text-xs font-bold text-slate-950">The selected run is no longer available</p>
              <p className="mt-0.5 text-[11px] text-slate-600">
                {failureCopy(selection.errorCode)} Nothing from a previous selection is shown in its place.
              </p>
            </div>
            <button
              type="button"
              onClick={() => void loadRun(null, null)}
              className="rounded-lg border border-slate-900 bg-white px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-slate-900 hover:bg-slate-50"
            >
              Open latest run
            </button>
          </div>
        )}

        {selection.status === "UNAVAILABLE" && (
          <div role="alert" className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-slate-300 bg-slate-100 px-4 py-3 sm:px-6">
            <div>
              <p className="text-xs font-bold text-slate-950">Dashboard data is temporarily unavailable</p>
              <p className="mt-0.5 text-[11px] text-slate-600">
                {failureCopy(selection.errorCode)} The last visible view is not treated as current.
              </p>
            </div>
            <button type="button" onClick={retryDashboardLoad} className="rounded-lg border border-slate-900 bg-white px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-slate-900 hover:bg-slate-50">
              Retry dashboard
            </button>
          </div>
        )}

        {selection.status === "EMPTY" && (
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3 sm:px-6">
            <div>
              <p className="text-xs font-bold text-slate-950">No reconciliation run yet</p>
              <p className="mt-0.5 text-[11px] text-slate-500">Import gateway, bank, and ledger evidence to create the first persisted run.</p>
            </div>
            <button type="button" onClick={() => setConnectDatasetOpen(true)} className="rounded-lg bg-slate-950 px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-white hover:bg-slate-800">
              Import evidence
            </button>
          </div>
        )}

        {/* A zero-exception run is a success, not a broken view. */}
        {selection.status === "READY" && cases.length === 0 && (
          <div
            data-testid="clean-run-banner"
            className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-emerald-200 bg-emerald-50/70 px-4 py-3 sm:px-6"
          >
            <div>
              <p className="text-xs font-bold text-emerald-900">
                Clean reconciliation · no exceptions raised
              </p>
              <p className="mt-0.5 text-[11px] text-emerald-800">
                This run completed with {formatCount(telemetry?.eligible)} eligible records and zero
                exception cases. There is nothing to investigate or approve.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setActiveTab("matrix")}
              className="rounded-lg border border-emerald-700 bg-white px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-emerald-900 hover:bg-emerald-50"
            >
              Review matched records
            </button>
          </div>
        )}

        {/* ============================ Distinct Dedicated Views ============================ */}
        {activeTab === "home" && (
          <div className="flex flex-1 flex-col overflow-hidden bg-slate-50/40 p-4 sm:p-6">
            <HomeChat
              telemetry={telemetry}
              runStatus={selection.status}
              caseCounts={{
                total: cases.length,
                awaitingApproval: approvalCases.length,
                verified: verifiedCases.length,
                unresolved: unresolvedCases.length,
              }}
              onRetry={retryDashboardLoad}
              onOpenView={setActiveTab}
            />
          </div>
        )}

        {/* ============================ 5-Way Master Matrix View ============================ */}
        {activeTab === "matrix" && (
          <MasterMatrixTable runId={activeRunId} />
        )}

        {/* ============================ Approval Queue ============================ */}
        {/* Only verifier-backed dry-run proposals awaiting a human appear here.
            The rail lists APPROVAL_REQUIRED cases; the pane additionally
            requires the dossier to carry a PASS proof and a dry-run, so a case
            can never be authorized from this view without both. */}
        {activeTab === "approval_queue" && (
          <QueueView
            tone="amber"
            icon={<IconShield size={17} />}
            title="Human approval queue"
            count={approvalCases.length}
            countLabel="awaiting authorization"
            description="A correction applies only after explicit human authorization of the exact verified proposal shown."
            cases={approvalCases}
            railTitle="Approval cases"
            statusFilter={CaseStatus.APPROVAL_REQUIRED}
            categoryFilter={categoryFilter}
            onCategoryFilter={setCategoryFilter}
            searchQuery={searchQuery}
            onSearchQuery={setSearchQuery}
            loading={booting}
            selectedCaseId={selectedCaseId}
            onSelect={openCase}
            notice={casePaneNotice}
            emptyTitle={
              selection.status === "READY" && cases.length === 0
                ? "Nothing to approve · clean run"
                : "No proposals awaiting approval"
            }
            emptyDetail={
              selection.status === "READY" && cases.length === 0
                ? "This run raised no exceptions, so there is no correction to authorize."
                : "Every verified proposal in this run has already been authorized or rejected."
            }
            body={
              caseDetail &&
              caseDetail.case.status === CaseStatus.APPROVAL_REQUIRED &&
              caseDetail.proof?.verifier_status === "PASS" &&
              caseDetail.dry_run ? (
                <CaseWorkspace
                  detail={caseDetail}
                  onApprove={() => {
                    setModalAction("APPROVE");
                    setModalOpen(true);
                  }}
                  onReject={() => {
                    setModalAction("REJECT");
                    setModalOpen(true);
                  }}
                />
              ) : caseDetail && caseDetail.case.status === CaseStatus.APPROVAL_REQUIRED ? (
                <CasePane
                  title="This case cannot be authorized from here"
                  detail="Approval requires a deterministic verifier PASS and a dry-run preview. This case is missing one of them, so no authorization action is offered."
                />
              ) : null
            }
          />
        )}

        {/* ============================ Verified Resolved ============================ */}
        {/* Persisted verified outcomes only. No AI confidence appears here. */}
        {activeTab === "verified_resolved" && (
          <QueueView
            tone="emerald"
            icon={<IconCheck size={17} />}
            title="Verified resolutions"
            count={verifiedCases.length}
            countLabel="closed"
            description="Persisted outcomes that reached a deterministic verifier PASS with cited evidence and rule versions. Model confidence is never a closure reason."
            cases={verifiedCases}
            railTitle="Verified cases"
            statusFilter="ALL"
            categoryFilter={categoryFilter}
            onCategoryFilter={setCategoryFilter}
            searchQuery={searchQuery}
            onSearchQuery={setSearchQuery}
            loading={booting}
            selectedCaseId={selectedCaseId}
            onSelect={openCase}
            notice={casePaneNotice}
            emptyTitle={
              selection.status === "READY" && cases.length === 0
                ? "No exceptions to resolve · clean run"
                : "No verified resolutions yet"
            }
            emptyDetail={
              selection.status === "READY" && cases.length === 0
                ? "This run reconciled without raising an exception, so there is nothing to resolve."
                : "A case appears here once a verifier PASS is persisted for it."
            }
            body={
              caseDetail &&
              (caseDetail.case.status === CaseStatus.VERIFIED_RESOLVED ||
                caseDetail.case.status === CaseStatus.SIMULATED_APPLIED) ? (
                <CaseWorkspace
                  detail={caseDetail}
                  onApprove={() => {
                    setModalAction("APPROVE");
                    setModalOpen(true);
                  }}
                  onReject={() => {
                    setModalAction("REJECT");
                    setModalOpen(true);
                  }}
                />
              ) : null
            }
          />
        )}

        {/* ============================ Unresolved Cases ============================ */}
        {/* Ambiguity is preserved, with the honest reason it stayed open. */}
        {activeTab === "unresolved" && (
          <QueueView
            tone="rose"
            icon={<IconFlag size={17} />}
            title="Unresolved exceptions"
            count={unresolvedCases.length}
            countLabel="left open"
            description="Cases that could not be closed: non-unique evidence, missing records, or a failed investigation. Ambiguity is never overridden by model confidence."
            cases={unresolvedCases}
            railTitle="Unresolved cases"
            statusFilter={CaseStatus.UNRESOLVED}
            categoryFilter={categoryFilter}
            onCategoryFilter={setCategoryFilter}
            searchQuery={searchQuery}
            onSearchQuery={setSearchQuery}
            loading={booting}
            selectedCaseId={selectedCaseId}
            onSelect={openCase}
            notice={casePaneNotice}
            emptyTitle={
              selection.status === "READY" && cases.length === 0
                ? "No exceptions raised · clean run"
                : "No unresolved exceptions"
            }
            emptyDetail={
              selection.status === "READY" && cases.length === 0
                ? "This run reconciled every eligible record deterministically."
                : "Every exception in this run reached a verified outcome or is awaiting approval."
            }
            body={
              caseDetail && caseDetail.case.status === CaseStatus.UNRESOLVED ? (
                <div className="space-y-5">
                  <UnresolvedReasonPanel detail={caseDetail} />
                  <CaseWorkspace
                    detail={caseDetail}
                    onApprove={() => {
                      setModalAction("APPROVE");
                      setModalOpen(true);
                    }}
                    onReject={() => {
                      setModalAction("REJECT");
                      setModalOpen(true);
                    }}
                  />
                </div>
              ) : null
            }
          />
        )}

        {/* ============================ Case Dossier View ============================ */}
        {activeTab === "dossier" && (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            {/* Telemetry Strip */}
            {!booting && telemetry && (
              <div className="grid shrink-0 grid-cols-2 gap-x-4 gap-y-3 border-b border-slate-200 bg-white px-6 py-3 sm:grid-cols-3 xl:grid-cols-7">
                <Metric
                  label="Active batch"
                  value={shortHash(telemetry.runId, 18)}
                  mono={false}
                  sub={
                    <span className="inline-flex items-center gap-1.5">
                      <span className={`rounded px-1 py-px font-mono text-[9px] uppercase tracking-wide ${telemetry.mode === "agent" ? "bg-blue-50 text-blue-700 border border-blue-200" : "bg-slate-100 text-slate-600"}`}>
                        {telemetry.mode}
                      </span>
                      {telemetry.status.toLowerCase()}
                    </span>
                  }
                />
                <Metric
                  label="Eligible records"
                  value={formatCount(telemetry.eligible)}
                  sub={
                    telemetry.quarantined !== undefined
                      ? `${formatCount(telemetry.quarantined)} quarantined`
                      : undefined
                  }
                />
                <Metric label="Deterministic match rate" tone="positive" value={telemetry.matchRate} />
                <Metric
                  label="Exception cases"
                  tone="warning"
                  value={formatCount(telemetry.casesCount)}
                  sub={`${cases.length} in queue`}
                />
                <Metric
                  label="Residual variance"
                  tone="critical"
                  value={telemetry.residualVariance !== undefined ? formatINR(telemetry.residualVariance) : "\u2014"}
                />
                <Metric
                  label="Throughput"
                  value={
                    telemetry.recordsPerSecond !== undefined
                      ? `${formatCount(Math.round(telemetry.recordsPerSecond))} rec/s`
                      : "\u2014"
                  }
                  sub={
                    telemetry.totalSeconds !== undefined
                      ? `${telemetry.totalSeconds.toFixed(2)} s total`
                      : undefined
                  }
                />
                <Metric
                  label="Economic integrity"
                  value={
                    telemetry.economicOutputHash ? (
                      <span title={telemetry.economicOutputHash}>{shortHash(telemetry.economicOutputHash)}</span>
                    ) : (
                      "\u2014"
                    )
                  }
                  tone={telemetry.economicOutputHash ? "positive" : "default"}
                  sub={telemetry.economicOutputHash ? "SHA-256 · runtime output" : "digest unavailable"}
                />
              </div>
            )}

            <div className="flex min-h-0 flex-1">
              <CaseRail
                cases={cases}
                loading={booting}
                selectedCaseId={selectedCaseId}
                onSelect={openCase}
                statusFilter={statusFilter}
                onStatusFilter={setStatusFilter}
                categoryFilter={categoryFilter}
                onCategoryFilter={setCategoryFilter}
                searchQuery={searchQuery}
                onSearchQuery={setSearchQuery}
                title="Exception Queue"
                hideStatusFilters={false}
              />
              <main className="min-w-0 flex-1 overflow-y-auto bg-[#f8fafc] p-6">
                {casePaneNotice ??
                  (caseDetail ? (
                    <CaseWorkspace
                      detail={caseDetail}
                      onApprove={() => {
                        setModalAction("APPROVE");
                        setModalOpen(true);
                      }}
                      onReject={() => {
                        setModalAction("REJECT");
                        setModalOpen(true);
                      }}
                    />
                  ) : (
                    <CasePane
                      icon={<IconScroll size={22} />}
                      title={cases.length === 0 ? "No exception cases in this run" : "No case file open"}
                      detail={
                        cases.length === 0
                          ? "This run raised no exceptions. There is no dossier to open, which is the expected result for a clean reconciliation."
                          : "Select an exception from the queue to open its dossier, cited evidence, and deterministic proof."
                      }
                    />
                  ))}
              </main>
            </div>
          </div>
        )}

        {/* ============================ Evidence Trace ============================ */}
        {/* The header describes the evidence this case actually cites. It used
            to assert a "cryptographically linked chain of gateway events,
            settlement batches, bank feeds and rule validations" regardless of
            what was cited, or whether any of it resolved. */}
        {activeTab === "evidence" && (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <ViewHeader
              tone="slate"
              icon={<IconRoute size={17} />}
              title="Evidence trace"
              caseId={caseDetail?.case.case_id ?? null}
              description="Records this case cites, each resolved to the immutable source row and content hash behind it."
            />

            <div className="flex min-h-0 flex-1">
              <CaseRail
                cases={cases}
                loading={booting}
                selectedCaseId={selectedCaseId}
                onSelect={openCase}
                statusFilter="ALL"
                categoryFilter={categoryFilter}
                onCategoryFilter={setCategoryFilter}
                searchQuery={searchQuery}
                onSearchQuery={setSearchQuery}
                title="Trace selector"
                hideStatusFilters={true}
              />
              <main className="min-w-0 flex-1 overflow-y-auto bg-[#f8fafc] p-6">
                {casePaneNotice ??
                  (caseDetail ? (
                    <div className="space-y-5">
                      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="select-all font-mono text-sm font-bold text-slate-900">
                              {caseDetail.case.case_id}
                            </span>
                            <StatusBadge status={caseDetail.case.status} />
                          </div>
                          <span className="font-mono text-[10px] text-slate-500">
                            run {caseDetail.case.run_id}
                          </span>
                        </div>
                        <EvidenceChain evidence={caseDetail.case.evidence} />
                      </div>
                      {caseDetail.proof && (
                        <ProofProvenancePanel detail={caseDetail} />
                      )}
                    </div>
                  ) : (
                    <CasePane
                      icon={<IconRoute size={22} />}
                      title={cases.length === 0 ? "No cited evidence in this run" : "Select a case to trace"}
                      detail={
                        cases.length === 0
                          ? "Evidence traces are built from exception cases. This run raised none."
                          : "Choose an exception to see the records it cites and where each one came from."
                      }
                    />
                  ))}
              </main>
            </div>
          </div>
        )}

        {/* ============================ Ledger Dry-Run ============================ */}
        {/* Shows the exact signed integer paise before, delta and after. The
            view used to show the delta alone, with "New Simulated Correction"
            and "DEFAULT_SETTLEMENT" standing in for null backend values, and
            told the operator an ambiguous case needed no correction. */}
        {activeTab === "ledger" && (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <ViewHeader
              tone="slate"
              icon={<IconScale size={17} />}
              title="Ledger dry-run"
              caseId={caseDetail?.case.case_id ?? null}
              description="Previewed effect in signed integer paise. Imported entries are never modified; applying adds one linked simulated entry."
            />

            <div className="flex min-h-0 flex-1">
              <CaseRail
                cases={cases}
                loading={booting}
                selectedCaseId={selectedCaseId}
                onSelect={openCase}
                statusFilter="ALL"
                categoryFilter={categoryFilter}
                onCategoryFilter={setCategoryFilter}
                searchQuery={searchQuery}
                onSearchQuery={setSearchQuery}
                title="Ledger cases"
                hideStatusFilters={true}
              />
              <main className="min-w-0 flex-1 overflow-y-auto bg-[#f8fafc] p-6">
                {casePaneNotice ??
                  (caseDetail?.dry_run ? (
                    <LedgerDryRunPanel
                      detail={caseDetail}
                      onApprove={() => {
                        setModalAction("APPROVE");
                        setModalOpen(true);
                      }}
                      onReject={() => {
                        setModalAction("REJECT");
                        setModalOpen(true);
                      }}
                    />
                  ) : caseDetail ? (
                    <NoDryRunPanel detail={caseDetail} />
                  ) : (
                    <CasePane
                      icon={<IconScale size={22} />}
                      title={cases.length === 0 ? "No correction proposed in this run" : "Select a case"}
                      detail={
                        cases.length === 0
                          ? "A dry-run exists only for an exception with a verified proposal. This run raised no exceptions."
                          : "Choose a case to inspect its previewed ledger effect."
                      }
                    />
                  ))}
              </main>
            </div>
          </div>
        )}

        {/* ============================ Audit Trail ============================ */}
        {/* Run scope and case scope are separate claims, so they are separate
            panels. The run's economic output hash used to be stamped beside a
            case-scoped trail as though it attested to it. */}
        {activeTab === "audit" && (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <ViewHeader
              tone="slate"
              icon={<IconScroll size={17} />}
              title="Audit trail"
              caseId={caseDetail?.case.case_id ?? null}
              description="Append-only events in the backend's authoritative storage order. Each row shows its sequence and SHA-256 digest."
            />

            <div className="flex min-h-0 flex-1">
              <CaseRail
                cases={cases}
                loading={booting}
                selectedCaseId={selectedCaseId}
                onSelect={openCase}
                statusFilter="ALL"
                categoryFilter={categoryFilter}
                onCategoryFilter={setCategoryFilter}
                searchQuery={searchQuery}
                onSearchQuery={setSearchQuery}
                title="Audit selector"
                hideStatusFilters={true}
              />
              <main className="min-w-0 flex-1 space-y-5 overflow-y-auto bg-[#f8fafc] p-6">
                {activeRunId ? (
                  <AuditLog
                    events={selection.runAudit}
                    scopeLabel={`run ${activeRunId}`}
                    emptyMessage="This run recorded no audit events."
                    loading={booting}
                  />
                ) : (
                  <CasePane
                    icon={<IconScroll size={22} />}
                    title="No run selected"
                    detail="An audit trail is scoped to one run. Select or import a run first."
                  />
                )}

                {selectedCaseId && (
                  <AuditLog
                    events={selection.caseAudit}
                    scopeLabel={`case ${selectedCaseId} in run ${activeRunId ?? "—"}`}
                    emptyMessage="This case recorded no audit events of its own."
                    loading={selection.caseStatus === "LOADING"}
                  />
                )}
              </main>
            </div>
          </div>
        )}

        {/* ============================ MDR & GST Fee Audit ============================ */}
        {activeTab === "fee_audit" && (
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-[#f8fafc] p-6">
            <div className="mx-auto w-full max-w-5xl space-y-6">
              <FeeAuditCard runId={activeRunId} />
            </div>
          </div>
        )}

        {/* ============================ API Status ============================ */}
        {activeTab === "api_status" && <ApiStatusPanel />}

        {/* ============================ Overlays ============================ */}
        <ConnectDatasetModal
          open={connectDatasetOpen}
          onClose={() => setConnectDatasetOpen(false)}
          onSyncSuccess={(runId) => {
            setActiveTab("dossier");
            setStatusFilter("ALL");
            // Load the exact run the workflow produced, not "the latest".
            void loadRun(runId, null);
            setToast({
              message: `Reconciled run ${runId} is now the selected run.`,
              kind: "success",
            });
          }}
        />
        <ExecutiveDossierModal
          open={dossierModalOpen}
          onClose={() => setDossierModalOpen(false)}
          runId={activeRunId}
        />
        {modalOpen && caseDetail && (
          <ApprovalModal
            detail={caseDetail}
            action={modalAction}
            busy={actionBusy}
            onClose={() => setModalOpen(false)}
            onConfirm={(decision) => void confirmAuthority(decision)}
          />
        )}
        <Toast toast={toast} onDismiss={() => setToast(null)} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Shared view scaffolding                                             */
/* ------------------------------------------------------------------ */

const HEADER_TONES = {
  slate: "border-slate-200 bg-white",
  amber: "border-amber-200/70 bg-amber-50/60",
  emerald: "border-emerald-200/70 bg-emerald-50/60",
  rose: "border-rose-200/70 bg-rose-50/60",
} as const;

const BADGE_TONES = {
  slate: "bg-slate-100 text-slate-700",
  amber: "bg-amber-200/80 text-amber-900",
  emerald: "bg-emerald-200/80 text-emerald-900",
  rose: "bg-rose-200/80 text-rose-900",
} as const;

type ViewTone = keyof typeof HEADER_TONES;

/** One compact header per view, carrying the selected identity. */
function ViewHeader({
  tone,
  icon,
  title,
  description,
  count,
  countLabel,
  caseId,
}: {
  tone: ViewTone;
  icon: React.ReactNode;
  title: string;
  description: string;
  count?: number;
  countLabel?: string;
  caseId?: string | null;
}) {
  return (
    <div className={`flex shrink-0 items-start gap-3 border-b px-6 py-3 ${HEADER_TONES[tone]}`}>
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-white text-slate-800 shadow-2xs">
        {icon}
      </span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-bold text-slate-900">{title}</h2>
          {count !== undefined && (
            <span
              className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${BADGE_TONES[tone]}`}
            >
              {formatCount(count)} {countLabel}
            </span>
          )}
          {caseId && (
            <span className="select-all rounded bg-slate-100 px-2 py-0.5 font-mono text-[11px] font-semibold text-slate-800">
              {caseId}
            </span>
          )}
        </div>
        <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-slate-600">{description}</p>
      </div>
    </div>
  );
}

/** A centred state message inside a case pane. */
function CasePane({
  title,
  detail,
  icon,
  action,
}: {
  title: string;
  detail: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex h-full items-center justify-center p-8 text-center" aria-live="polite">
      <div className="max-w-sm">
        {icon && (
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-400 shadow-sm">
            {icon}
          </div>
        )}
        <p className="text-sm font-bold text-slate-800">{title}</p>
        <p className="mt-1.5 text-xs leading-relaxed text-slate-500">{detail}</p>
        {action}
      </div>
    </div>
  );
}

/** Rail + pane layout shared by the three case queues. */
function QueueView({
  tone,
  icon,
  title,
  description,
  count,
  countLabel,
  cases,
  railTitle,
  statusFilter,
  categoryFilter,
  onCategoryFilter,
  searchQuery,
  onSearchQuery,
  loading,
  selectedCaseId,
  onSelect,
  notice,
  body,
  emptyTitle,
  emptyDetail,
}: {
  tone: ViewTone;
  icon: React.ReactNode;
  title: string;
  description: string;
  count: number;
  countLabel: string;
  cases: CaseSummary[];
  railTitle: string;
  statusFilter: string;
  categoryFilter: string;
  onCategoryFilter: (value: string) => void;
  searchQuery: string;
  onSearchQuery: (value: string) => void;
  loading: boolean;
  selectedCaseId: string | null;
  onSelect: (caseId: string) => void;
  notice: React.ReactNode;
  body: React.ReactNode;
  emptyTitle: string;
  emptyDetail: string;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <ViewHeader
        tone={tone}
        icon={icon}
        title={title}
        description={description}
        count={count}
        countLabel={countLabel}
      />
      <div className="flex min-h-0 flex-1">
        <CaseRail
          cases={cases}
          loading={loading}
          selectedCaseId={selectedCaseId}
          onSelect={onSelect}
          statusFilter={statusFilter}
          categoryFilter={categoryFilter}
          onCategoryFilter={onCategoryFilter}
          searchQuery={searchQuery}
          onSearchQuery={onSearchQuery}
          title={railTitle}
          hideStatusFilters={true}
        />
        <main className="min-w-0 flex-1 overflow-y-auto bg-[#f8fafc] p-6">
          {notice ?? body ?? <CasePane icon={icon} title={emptyTitle} detail={emptyDetail} />}
        </main>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Ledger dry-run                                                      */
/* ------------------------------------------------------------------ */

/**
 * Exact signed integer paise before, delta and after.
 *
 * Every figure comes from the persisted dry-run row. Where the backend stored
 * no target entry or account code, that is stated as "not set by the dry-run"
 * rather than filled in with a plausible-looking default.
 */
function LedgerDryRunPanel({
  detail,
  onApprove,
  onReject,
}: {
  detail: CaseDetail;
  onApprove: () => void;
  onReject: () => void;
}) {
  const dry = detail.dry_run;
  if (!dry) return null;
  const proof = detail.proof;
  const applied = detail.simulated_correction;
  const awaitingApproval = detail.case.status === CaseStatus.APPROVAL_REQUIRED;
  const authorizable = awaitingApproval && proof?.verifier_status === "PASS";

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <header className="mb-4">
          <h3 className="text-sm font-bold text-slate-900">Simulated correction preview</h3>
          <p className="mt-1 text-xs leading-relaxed text-slate-600">
            {proof?.claim ?? "No verifier claim is recorded for this preview."}
          </p>
        </header>

        <div
          data-testid="ledger-before-delta-after"
          className="overflow-hidden rounded-xl border border-slate-200"
        >
          <div className="grid grid-cols-1 divide-y divide-slate-200 bg-slate-50 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
            <div className="px-4 py-3.5">
              <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
                Variance before
              </div>
              <div
                data-testid="ledger-variance-before"
                className="mt-1.5 font-mono text-lg font-bold tabular-nums text-amber-700"
              >
                {formatINR(dry.variance_before_paise)}
              </div>
            </div>
            <div className="px-4 py-3.5">
              <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
                Proposed delta
              </div>
              <div
                data-testid="ledger-proposed-delta"
                className="mt-1.5 font-mono text-lg font-bold tabular-nums text-slate-900"
              >
                {formatSignedINR(dry.proposed_delta_paise)}
              </div>
            </div>
            <div className="px-4 py-3.5">
              <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
                Variance after
              </div>
              <div
                data-testid="ledger-variance-after"
                className={`mt-1.5 font-mono text-lg font-bold tabular-nums ${
                  dry.variance_after_paise === 0 ? "text-emerald-700" : "text-rose-700"
                }`}
              >
                {formatINR(dry.variance_after_paise)}
              </div>
            </div>
          </div>
        </div>

        <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Fact label="Target ledger entry" value={dry.target_ledger_entry_id} />
          <Fact label="Target account" value={dry.account_code} />
          <Fact label="Preview state" value={dry.status} />
        </dl>

        {dry.warnings.length > 0 && (
          <ul className="mt-4 space-y-1.5">
            {dry.warnings.map((warning, index) => (
              <li
                key={index}
                className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900"
              >
                {warning}
              </li>
            ))}
          </ul>
        )}

        {dry.uncertainty.length > 0 && (
          <ul className="mt-3 space-y-1.5">
            {dry.uncertainty.map((item, index) => (
              <li
                key={index}
                className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700"
              >
                {item}
              </li>
            ))}
          </ul>
        )}

        <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-100 pt-3 font-mono text-[10px] text-slate-500">
          <span>correction {dry.correction_id}</span>
          <span>proof {dry.proof_id}</span>
          {proof && (
            <span>
              {proof.verifier_rule_id} v{proof.verifier_rule_version} · {proof.verifier_status}
            </span>
          )}
          <span>previewed {formatUtc(dry.created_at_utc)}</span>
          <span>simulation only · no external write</span>
        </div>

        {applied ? (
          <p
            data-testid="ledger-applied-notice"
            className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs text-emerald-900"
          >
            <strong>Already applied.</strong> One linked simulated entry exists:{" "}
            <span className="select-all font-mono">{applied.correction_id}</span> for{" "}
            <span className="font-mono">{formatSignedINR(applied.delta_paise)}</span> at{" "}
            {formatUtc(applied.applied_at_utc)}. Repeating the authorization reuses this entry; it
            never creates a second one.
          </p>
        ) : authorizable ? (
          <div className="mt-6 flex flex-wrap justify-end gap-3 border-t border-slate-100 pt-4">
            <button
              type="button"
              onClick={onReject}
              className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-slate-700 transition-colors hover:bg-slate-50"
            >
              Reject proposal
            </button>
            <button
              type="button"
              onClick={onApprove}
              className="rounded-lg bg-slate-950 px-4 py-2 text-xs font-bold text-white shadow-xs transition-colors hover:bg-slate-800"
            >
              Authorize simulated correction
            </button>
          </div>
        ) : awaitingApproval ? (
          <p className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-700">
            This preview cannot be authorized: the recorded verifier status is{" "}
            <span className="font-mono">{proof?.verifier_status ?? "absent"}</span>, and approval
            requires a deterministic PASS.
          </p>
        ) : null}
      </section>
    </div>
  );
}

/** Why a selected case has no ledger preview — the honest reason, per status. */
function NoDryRunPanel({ detail }: { detail: CaseDetail }) {
  const status = detail.case.status;
  const reasons = detail.case.reason_codes;
  const isUnresolved = status === CaseStatus.UNRESOLVED;

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <h3 className="text-sm font-bold text-slate-900">
        {isUnresolved
          ? "No correction can be previewed for an unresolved case"
          : "No ledger preview exists for this case"}
      </h3>
      <p className="mt-1.5 text-xs leading-relaxed text-slate-600">
        {isUnresolved
          ? "A dry-run is only produced after a deterministic verifier PASS. This case is deliberately unresolved, so no correction is proposed and none should be inferred."
          : "This case has no persisted dry-run row. A correction is previewed only once a verifier PASS is recorded."}
      </p>

      {reasons.length > 0 && (
        <div className="mt-4">
          <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
            Recorded reasons
          </p>
          <ul className="mt-1.5 flex flex-wrap gap-1.5">
            {reasons.map((reason) => (
              <li
                key={reason}
                className="rounded border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-[11px] text-slate-700"
              >
                {reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Fact label="Case status" value={status} />
        <Fact label="Verifier status" value={detail.proof?.verifier_status ?? null} />
        <Fact
          label="Signed case variance"
          value={formatSignedINR(detail.case.variance_paise)}
        />
      </dl>
    </section>
  );
}

/** Why an unresolved case stayed open, from persisted data only. */
function UnresolvedReasonPanel({ detail }: { detail: CaseDetail }) {
  const proof = detail.proof;
  const failedHypotheses = detail.hypotheses.filter((item) => item.status !== "SUPPORTED");
  const missingEvidence = detail.case.evidence.filter((item) => item.resolution !== "RESOLVED");

  return (
    <section className="rounded-2xl border border-rose-200 bg-rose-50/50 p-5">
      <h3 className="text-sm font-bold text-rose-950">Why this case is unresolved</h3>
      <p className="mt-1 text-xs leading-relaxed text-rose-900">
        Recorded reasons only. No explanation is inferred, and no model confidence can close this
        case.
      </p>

      <ul className="mt-3 space-y-1.5">
        {detail.case.reason_codes.map((reason) => (
          <li
            key={reason}
            className="rounded-lg border border-rose-200 bg-white px-3 py-2 font-mono text-[11px] font-semibold text-rose-900"
          >
            {reason}
          </li>
        ))}
        {proof?.verifier_status && proof.verifier_status !== "PASS" && (
          <li className="rounded-lg border border-rose-200 bg-white px-3 py-2 text-[11px] text-rose-900">
            Verifier <span className="font-mono">{proof.verifier_rule_id}</span> v
            {proof.verifier_rule_version} returned{" "}
            <span className="font-mono font-bold">{proof.verifier_status}</span>.
          </li>
        )}
        {proof?.uncertainty.map((item, index) => (
          <li
            key={`uncertainty-${index}`}
            className="rounded-lg border border-rose-200 bg-white px-3 py-2 text-[11px] text-rose-900"
          >
            {item}
          </li>
        ))}
        {missingEvidence.length > 0 && (
          <li className="rounded-lg border border-rose-200 bg-white px-3 py-2 text-[11px] text-rose-900">
            {missingEvidence.length} cited record
            {missingEvidence.length === 1 ? "" : "s"} did not resolve to a source row in this run.
          </li>
        )}
        {failedHypotheses.length > 0 && (
          <li className="rounded-lg border border-rose-200 bg-white px-3 py-2 text-[11px] text-rose-900">
            {failedHypotheses.length} recorded hypothes
            {failedHypotheses.length === 1 ? "is was" : "es were"} not supported by the evidence.
          </li>
        )}
        {detail.case.reason_codes.length === 0 &&
          !proof &&
          missingEvidence.length === 0 &&
          failedHypotheses.length === 0 && (
            <li className="rounded-lg border border-rose-200 bg-white px-3 py-2 text-[11px] text-rose-900">
              No investigation was recorded for this case, so it has no verified explanation.
            </li>
          )}
      </ul>
    </section>
  );
}

/** Proof identity and provenance for the evidence view. */
function ProofProvenancePanel({ detail }: { detail: CaseDetail }) {
  const proof = detail.proof;
  if (!proof) return null;
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="text-sm font-bold text-slate-900">Proof provenance</h3>
      <p className="mt-1 text-xs leading-relaxed text-slate-600">{proof.claim}</p>

      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Proof" value={proof.proof_id} />
        <Fact
          label="Verifier rule"
          value={`${proof.verifier_rule_id} v${proof.verifier_rule_version}`}
        />
        <Fact label="Verifier status" value={proof.verifier_status} />
        <Fact label="Authority decision" value={proof.authority_decision} />
      </dl>

      {proof.supported_evidence.length > 0 && (
        <EvidenceIdList label="Supported by" ids={proof.supported_evidence} />
      )}
      {proof.conflicting_evidence.length > 0 && (
        <EvidenceIdList label="Conflicting" ids={proof.conflicting_evidence} />
      )}

      <p
        className="mt-4 select-all break-all border-t border-slate-100 pt-3 font-mono text-[10px] text-slate-500"
        title={proof.canonical_hash}
      >
        canonical proof hash {proof.canonical_hash}
      </p>
    </section>
  );
}

function EvidenceIdList({ label, ids }: { label: string; ids: string[] }) {
  return (
    <div className="mt-3">
      <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500">{label}</p>
      <ul className="mt-1.5 flex flex-wrap gap-1.5">
        {ids.map((id) => (
          <li
            key={id}
            className="select-all rounded border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-[11px] text-slate-700"
          >
            {id}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * One labelled fact.
 *
 * A null value is stated as unset by the backend. It is never replaced with a
 * placeholder that reads like real data.
 */
function Fact({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
      <dt className="text-[10px] font-bold uppercase tracking-wider text-slate-500">{label}</dt>
      <dd
        className={`mt-1 break-all font-mono text-xs font-bold ${
          value ? "text-slate-900" : "text-slate-400"
        }`}
      >
        {value || "not set by the dry-run"}
      </dd>
    </div>
  );
}
