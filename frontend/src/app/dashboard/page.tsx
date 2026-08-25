"use client";

/**
 * ARGUS CONTROL control room.
 *
 * Clean, minimal, bright & professional light dashboard.
 * Renders backend results only: no financial truth logic lives here and no
 * metric is displayed unless the API produced it. Loading, empty, partial
 * failure, and retry states are first-class (PRD §13.4).
 */

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import type {
  AuditLogItem,
  CaseDetail,
  CaseSummary,
  ReconcileResponse,
  RunListItem,
} from "../../lib/types";
import { formatCount, formatINR, formatRate, shortHash } from "../../lib/format";
import { CaseRail, categoryMeta, StatusBadge } from "../../components/case-rail";
import { CaseWorkspace } from "../../components/case-workspace";
import { EvidenceChain } from "../../components/evidence-chain";
import { AuditLog } from "../../components/audit-log";
import { ApprovalModal } from "../../components/approval-modal";
import {
  IconActivity,
  IconBolt,
  IconBookOpen,
  IconCheck,
  IconChevronDown,
  IconFlag,
  IconHome,
  IconLayers,
  IconPresentation,
  IconRefresh,
  IconRoute,
  IconScale,
  IconScroll,
  IconShield,
  IconSidebar,
} from "../../components/icons";
import { Metric, Panel, Skeleton, Toast, type ToastState } from "../../components/primitives";
import { CaseStatus } from "../../domain/enums";

/* ------------------------------------------------------------------ */
/* Defensive readers for the open-ended run summary object             */
/* ------------------------------------------------------------------ */

type Summary = Record<string, unknown>;

function num(obj: Summary | undefined, key: string): number | undefined {
  const v = obj?.[key];
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

function str(obj: Summary | undefined, key: string): string | undefined {
  const v = obj?.[key];
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

function childObj(obj: Summary | undefined, key: string): Summary | undefined {
  const v = obj?.[key];
  return v !== null && typeof v === "object" ? (v as Summary) : undefined;
}

interface RunTelemetry {
  runId: string;
  status: string;
  mode: string;
  eligible?: number;
  matched?: number;
  matchRate: string;
  casesCount?: number;
  quarantined?: number;
  residualVariance?: number;
  grossVolume?: number;
  recordsPerSecond?: number;
  totalSeconds?: number;
  econHash?: string;
}

function telemetryFromRun(run: RunListItem): RunTelemetry {
  const s = run.summary ?? {};
  const rate = childObj(s, "runtime_match_rate");
  const totals = childObj(s, "financial_control_totals");
  const timing = childObj(s, "timing_metrics");
  return {
    runId: run.run_id,
    status: run.status,
    mode: str(s, "mode") ?? "rules-only",
    eligible: num(s, "eligible_record_count"),
    matched: num(s, "matched_record_count"),
    matchRate: formatRate(num(rate, "numerator") ?? NaN, num(rate, "denominator") ?? NaN),
    casesCount: num(s, "cases_count"),
    quarantined: num(s, "quarantined_row_count"),
    residualVariance: num(totals, "residual_abs_variance_paise"),
    grossVolume: num(totals, "payment_gross_paise"),
    recordsPerSecond: num(timing, "records_per_second"),
    totalSeconds: num(timing, "total_seconds"),
    econHash: str(s, "economic_output_hash"),
  };
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

type Tab = "investigation" | "evidence" | "audit";

export default function ControlRoomPage() {
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [caseDetail, setCaseDetail] = useState<CaseDetail | null>(null);
  const [auditTrail, setAuditTrail] = useState<AuditLogItem[]>([]);

  const [activeTab, setActiveTab] = useState<Tab>("investigation");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [categoryFilter, setCategoryFilter] = useState("ALL");
  const [searchQuery, setSearchQuery] = useState("");

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [buildSectionOpen, setBuildSectionOpen] = useState(true);
  const [investigationSectionOpen, setInvestigationSectionOpen] = useState(true);

  const [booting, setBooting] = useState(true);
  const [running, setRunning] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [apiOk, setApiOk] = useState<boolean | null>(null);

  const [modalAction, setModalAction] = useState<"APPROVE" | "REJECT">("APPROVE");
  const [modalOpen, setModalOpen] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);

  /* ----------------------------- fetching ------------------------- */

  const selectCase = useCallback(async (caseId: string) => {
    setSelectedCaseId(caseId);
    try {
      const [detailRes, auditRes] = await Promise.all([
        fetch(`/api/v1/cases/${caseId}`),
        fetch(`/api/v1/cases/${caseId}/audit`),
      ]);
      if (detailRes.ok) setCaseDetail((await detailRes.json()) as CaseDetail);
      if (auditRes.ok) setAuditTrail((await auditRes.json()) as AuditLogItem[]);
    } catch {
      /* partial view stays usable; toast surfaces hard failures elsewhere */
    }
  }, []);

  const loadCases = useCallback(
    async (runId: string) => {
      try {
        const res = await fetch(`/api/v1/runs/${runId}/cases`);
        if (!res.ok) return;
        const data = (await res.json()) as CaseSummary[];
        setCases(data);
        void selectCase(data[0]?.case_id ?? "");
      } catch {
        /* keep previous case list */
      }
    },
    [selectCase],
  );

  const loadRuns = useCallback(async (): Promise<boolean> => {
    try {
      const res = await fetch("/api/v1/runs");
      if (!res.ok) throw new Error(String(res.status));
      const data = (await res.json()) as RunListItem[];
      setRuns(data);
      setApiOk(true);
      if (data.length > 0 && data[0]) {
        setActiveRunId(data[0].run_id);
        await loadCases(data[0].run_id);
      }
      return true;
    } catch {
      setApiOk(false);
      return false;
    }
  }, [loadCases]);

  useEffect(() => {
    void loadRuns().finally(() => setBooting(false));
  }, [loadRuns]);

  async function triggerRun(profile: "dev" | "adversarial", mode: "rules-only" | "agent") {
    setRunning(true);
    try {
      const res = await fetch("/api/v1/runs/reconcile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dataset_profile: profile, mode, force: true }),
      });
      if (!res.ok) {
        const err = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(err.detail ?? `batch failed (${res.status})`);
      }
      const run = (await res.json()) as ReconcileResponse;
      setActiveRunId(run.run_id);
      await Promise.all([loadRuns(), loadCases(run.run_id)]);
      setToast({
        kind: "success",
        message: `Batch complete · ${run.run_id}${run.reused ? " (idempotent replay)" : ""}`,
      });
    } catch (e) {
      setToast({
        kind: "error",
        message: `Reconciliation failed: ${e instanceof Error ? e.message : String(e)}`,
      });
    } finally {
      setRunning(false);
    }
  }

  async function confirmAuthority(reviewerId: string, notes: string) {
    if (!selectedCaseId) return;
    setActionBusy(true);
    try {
      const endpoint = modalAction === "APPROVE" ? "approve" : "reject";
      const res = await fetch(`/api/v1/cases/${selectedCaseId}/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewer_id: reviewerId, notes }),
      });
      if (!res.ok) {
        const err = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(err.detail ?? `action failed (${res.status})`);
      }
      setModalOpen(false);
      setToast({
        kind: "success",
        message:
          modalAction === "APPROVE"
            ? `Simulated correction applied to ${selectedCaseId}`
            : `${selectedCaseId} preserved as unresolved`,
      });
      if (activeRunId) await loadCases(activeRunId);
      await selectCase(selectedCaseId);
    } catch (e) {
      setToast({
        kind: "error",
        message: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setActionBusy(false);
    }
  }

  /* ----------------------------- derived -------------------------- */

  const activeRun = runs.find((r) => r.run_id === activeRunId);
  const telemetry = activeRun ? telemetryFromRun(activeRun) : null;

  /* ----------------------------- chrome --------------------------- */

  const apiPill = (
    <span className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-[11px] font-semibold text-slate-700 shadow-2xs">
      <span aria-hidden className={`h-2 w-2 rounded-full ${apiOk === false ? "bg-rose-500" : apiOk ? "bg-emerald-500 animate-pulse-dot" : "bg-slate-400"}`} />
      {apiOk === false ? "API offline" : apiOk ? "API online" : "Connecting"}
    </span>
  );

  const syntheticPill = (
    <span className="hidden md:inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-[11px] font-semibold text-slate-600 shadow-2xs">
      <IconShield size={12} className="text-blue-600" />
      Tenant argus-demo · Synthetic data only
    </span>
  );

  return (
    <div className="flex h-screen overflow-hidden bg-[#f8fafc] text-slate-900 antialiased font-sans" suppressHydrationWarning>
      {/* ============================ Sarvam API Style Clean Sidebar ============================ */}
      <aside
        className={`flex shrink-0 flex-col border-r border-slate-200 bg-white transition-[width] duration-200 ease-in-out z-30 overflow-hidden select-none ${sidebarOpen ? "w-[240px]" : "w-[56px]"
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
              className="group flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition-colors"
            >
              <IconSidebar size={18} className="text-slate-500 transition-transform duration-200 group-hover:scale-110 group-hover:text-slate-900" />
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
                setActiveTab("investigation");
              }}
              title="Home"
              className={`group flex h-10 w-full items-center rounded-xl transition-all duration-150 overflow-hidden ${statusFilter === "ALL" && categoryFilter === "ALL" && activeTab === "investigation"
                ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                }`}
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                <IconHome size={18} className="text-slate-700 transition-transform duration-200 group-hover:scale-110 group-hover:text-blue-600" />
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
                  className={`transition-transform duration-300 ease-in-out ${
                    buildSectionOpen ? "rotate-180" : "rotate-0"
                  }`}
                />
              </button>
            ) : (
              <div className="my-2 border-t border-slate-100 mx-1" />
            )}

            <div
              className={`grid transition-[grid-template-rows,opacity] duration-300 ease-in-out ${
                buildSectionOpen || !sidebarOpen
                  ? "grid-rows-[1fr] opacity-100"
                  : "grid-rows-[0fr] opacity-0 pointer-events-none"
              }`}
            >
              <div className="min-h-0 space-y-0.5 overflow-hidden">
                <button
                  onClick={() => void triggerRun("dev", "rules-only")}
                  disabled={running || booting}
                  title="Reconcile Dev"
                  className="group flex h-10 w-full items-center rounded-xl text-slate-700 hover:bg-slate-100/80 hover:text-slate-900 transition-all disabled:opacity-50 overflow-hidden"
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconBolt size={17} className="text-slate-700 transition-transform duration-300 group-hover:scale-120 group-hover:text-amber-500 group-hover:rotate-12" />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px] font-medium">Reconcile Dev</span>
                  </div>
                </button>

                <button
                  onClick={() => void triggerRun("adversarial", "agent")}
                  disabled={running || booting}
                  title="AI Adversarial"
                  className="group flex h-10 w-full items-center rounded-xl text-slate-700 hover:bg-slate-100/80 hover:text-slate-900 transition-all disabled:opacity-50 overflow-hidden"
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconRoute size={17} className="text-slate-700 transition-transform duration-300 group-hover:scale-115 group-hover:text-purple-600 group-hover:rotate-12" />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px] font-medium">AI Adversarial</span>
                  </div>
                </button>

                <button
                  onClick={() => setStatusFilter(CaseStatus.APPROVAL_REQUIRED)}
                  title="Approval Queue"
                  className={`group flex h-10 w-full items-center rounded-xl transition-all overflow-hidden ${statusFilter === CaseStatus.APPROVAL_REQUIRED
                    ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                    : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
                    }`}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconShield size={17} className="text-amber-600 transition-transform duration-200 group-hover:scale-115 group-hover:-translate-y-0.5" />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px]">Approval Queue</span>
                  </div>
                </button>

                <button
                  onClick={() => setStatusFilter(CaseStatus.VERIFIED_RESOLVED)}
                  title="Verified Resolved"
                  className={`group flex h-10 w-full items-center rounded-xl transition-all overflow-hidden ${statusFilter === CaseStatus.VERIFIED_RESOLVED
                    ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                    : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
                    }`}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconCheck size={17} className="text-emerald-600 transition-transform duration-200 group-hover:scale-120 group-hover:text-emerald-500" />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px]">Verified Resolved</span>
                  </div>
                </button>

                <button
                  onClick={() => setStatusFilter(CaseStatus.UNRESOLVED)}
                  title="Unresolved Cases"
                  className={`group flex h-10 w-full items-center rounded-xl transition-all overflow-hidden ${statusFilter === CaseStatus.UNRESOLVED
                    ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                    : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
                    }`}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconFlag size={17} className="text-rose-600 transition-transform duration-200 group-hover:scale-120 group-hover:rotate-12 group-hover:text-rose-500" />
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
                  className={`transition-transform duration-300 ease-in-out ${
                    investigationSectionOpen ? "rotate-180" : "rotate-0"
                  }`}
                />
              </button>
            ) : (
              <div className="my-2 border-t border-slate-100 mx-1" />
            )}

            <div
              className={`grid transition-[grid-template-rows,opacity] duration-300 ease-in-out ${
                investigationSectionOpen || !sidebarOpen
                  ? "grid-rows-[1fr] opacity-100"
                  : "grid-rows-[0fr] opacity-0 pointer-events-none"
              }`}
            >
              <div className="min-h-0 space-y-0.5 overflow-hidden">
                <button
                  onClick={() => setActiveTab("investigation")}
                  title="Case Dossier"
                  className={`group flex h-10 w-full items-center rounded-xl transition-all overflow-hidden ${activeTab === "investigation"
                    ? "bg-slate-100 text-slate-900 font-semibold shadow-xs"
                    : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
                    }`}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconLayers size={17} className="text-slate-700 transition-transform duration-200 group-hover:scale-115 group-hover:text-blue-600" />
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
                    <IconRoute size={17} className="text-slate-700 transition-transform duration-200 group-hover:scale-115 group-hover:text-indigo-600 group-hover:rotate-12" />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px]">Evidence Trace</span>
                  </div>
                </button>

                <button
                  onClick={() => setActiveTab("investigation")}
                  title="Ledger Dry-Run"
                  className="group flex h-10 w-full items-center rounded-xl text-slate-700 hover:bg-slate-100/80 hover:text-slate-900 transition-all overflow-hidden"
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconScale size={17} className="text-slate-700 transition-transform duration-200 group-hover:scale-115 group-hover:text-teal-600 group-hover:rotate-6" />
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
                    <IconScroll size={17} className="text-slate-700 transition-transform duration-200 group-hover:scale-115 group-hover:text-cyan-600 group-hover:-translate-y-0.5" />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px]">Audit Trail</span>
                  </div>
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Sidebar Footer Resources */}
        <div className="border-t border-slate-100 p-2 space-y-1 bg-slate-50/50 shrink-0 overflow-x-hidden">
          <div
            title="API Status: Operational"
            className="group flex h-10 w-full items-center rounded-xl text-slate-700 overflow-hidden"
          >
            <div className="flex h-10 w-10 shrink-0 items-center justify-center">
              <IconActivity size={17} className="text-emerald-600 transition-transform duration-300 group-hover:scale-120 group-hover:text-emerald-500" />
            </div>
            <div
              className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                }`}
            >
              <span className="flex items-center gap-2 text-[13px] font-medium truncate">
                API Status
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse-dot" />
              </span>
            </div>
          </div>

          <Link
            href="/presentation"
            title="Presentation Mode"
            className="group flex h-10 w-full items-center rounded-xl text-slate-700 hover:bg-slate-100 hover:text-slate-900 transition-all overflow-hidden"
          >
            <div className="flex h-10 w-10 shrink-0 items-center justify-center">
              <IconPresentation size={17} className="text-slate-700 transition-transform duration-200 group-hover:scale-115 group-hover:text-blue-600" />
            </div>
            <div
              className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                }`}
            >
              <span className="truncate text-[13px] font-medium">Presentation</span>
            </div>
          </Link>

          <Link
            href="/"
            title="Documentation / Home"
            className="group flex h-10 w-full items-center rounded-xl text-slate-700 hover:bg-slate-100 hover:text-slate-900 transition-all overflow-hidden"
          >
            <div className="flex h-10 w-10 shrink-0 items-center justify-center">
              <IconBookOpen size={17} className="text-slate-700 transition-transform duration-200 group-hover:scale-115 group-hover:text-indigo-600" />
            </div>
            <div
              className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                }`}
            >
              <span className="truncate text-[13px] font-medium">Documentation</span>
            </div>
          </Link>
        </div>
      </aside>

      {/* ============================ Main Dashboard Area ============================ */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[#f8fafc]">
        {/* Top Header Bar */}
        <header className="z-20 flex h-14 shrink-0 flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-6">
          <div className="flex min-w-0 items-center gap-3">
            <h1 className="flex items-baseline gap-2 whitespace-nowrap text-[15px] font-bold text-slate-900">
              Argus Control
              <span className="font-mono text-[11px] font-medium text-slate-500">
                · Reconciliation Room
              </span>
            </h1>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            {syntheticPill}
            {apiPill}
            <button
              onClick={() => void triggerRun("dev", "rules-only")}
              disabled={running || booting}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-semibold text-slate-800 shadow-2xs hover:bg-slate-50 hover:border-slate-300 transition-all disabled:opacity-50"
            >
              {running ? (
                <span aria-hidden className="h-3 w-3 animate-spin rounded-full border-2 border-slate-400 border-t-slate-800" />
              ) : (
                <IconBolt size={13} className="text-amber-600" />
              )}
              {running ? "Reconciling…" : "Reconcile dev"}
            </button>
            <button
              onClick={() => void triggerRun("adversarial", "agent")}
              disabled={running || booting}
              className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3.5 py-1.5 text-xs font-bold text-white shadow-2xs hover:bg-slate-800 transition-all disabled:opacity-50"
            >
              <IconRoute size={13} className="text-blue-300" />
              AI adversarial batch
            </button>
          </div>
        </header>

        {/* Telemetry Strip (7 Clean Metric KPI Cards) */}
        {booting && (
          <div className="grid shrink-0 grid-cols-2 gap-3 border-b border-slate-200 bg-white px-6 py-3 sm:grid-cols-3 xl:grid-cols-7">
            {Array.from({ length: 7 }).map((_, i) => (
              <Skeleton key={i} className="h-14" />
            ))}
          </div>
        )}

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
                telemetry.econHash ? (
                  <span title={telemetry.econHash}>{shortHash(telemetry.econHash)}</span>
                ) : (
                  "\u2014"
                )
              }
              tone={telemetry.econHash ? "positive" : "default"}
              sub={telemetry.econHash ? "SHA-256 · sealed" : "unsigned output"}
            />
          </div>
        )}

        {!booting && !telemetry && (
          <div className="shrink-0 border-b border-slate-200 bg-white px-6 py-6">
            <Panel className="mx-auto max-w-md p-6 text-center shadow-sm">
              <p className="text-sm font-bold text-slate-900">No batches recorded yet</p>
              <p className="mt-1.5 text-xs leading-relaxed text-slate-500">
                Run a reconciliation batch to populate the flight recorder. The
                deterministic pipeline works without any model key configured.
              </p>
              <div className="mt-4 flex justify-center gap-2.5">
                <button
                  onClick={() => void triggerRun("dev", "rules-only")}
                  disabled={running}
                  className="rounded-lg bg-slate-900 px-4 py-2 text-xs font-bold text-white shadow-sm transition hover:bg-slate-800"
                >
                  Reconcile dev batch
                </button>
              </div>
            </Panel>
          </div>
        )}

        {/* ============================ Workspace =========================== */}
        <div className="flex min-h-0 flex-1">
          <CaseRail
            cases={cases}
            loading={booting}
            selectedCaseId={selectedCaseId}
            onSelect={(id) => {
              setActiveTab("investigation");
              void selectCase(id);
            }}
            statusFilter={statusFilter}
            onStatusFilter={setStatusFilter}
            categoryFilter={categoryFilter}
            onCategoryFilter={setCategoryFilter}
            searchQuery={searchQuery}
            onSearchQuery={setSearchQuery}
          />

          <main className="min-w-0 flex-1 overflow-y-auto bg-[#f8fafc]">
            {!caseDetail ? (
              <div className="flex h-full items-center justify-center p-8">
                <div className="max-w-sm text-center">
                  <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-400 shadow-sm">
                    <IconScroll size={22} />
                  </div>
                  <p className="text-sm font-bold text-slate-800">No case file open</p>
                  <p className="mt-1.5 text-xs leading-relaxed text-slate-500">
                    Select an exception from the queue to open its dossier,
                    hypotheses, deterministic proof, and audit trail.
                  </p>
                  {apiOk === false && (
                    <button
                      onClick={() => void loadRuns()}
                      className="mx-auto mt-4 inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 shadow-2xs hover:bg-slate-50"
                    >
                      <IconRefresh size={13} /> Retry connection
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <div className="space-y-4 p-6">
                {/* Case Header */}
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
                    <span className="select-all font-mono text-base font-bold tracking-tight text-slate-900">
                      {caseDetail.case.case_id}
                    </span>
                    <span aria-hidden className="text-slate-300">/</span>
                    <span
                      className="inline-flex items-center gap-1.5 text-xs font-semibold"
                      style={{ color: categoryMeta(caseDetail.case.category).hex }}
                    >
                      {categoryMeta(caseDetail.case.category).icon}
                      {categoryMeta(caseDetail.case.category).label}
                    </span>
                    <StatusBadge status={caseDetail.case.status} />
                  </div>

                  <nav aria-label="Workspace views" className="flex rounded-lg border border-slate-200 bg-slate-100 p-0.5">
                    {(
                      [
                        ["investigation", "Investigation", <IconShield key="i" size={13} />],
                        ["evidence", "Evidence trace", <IconRoute key="e" size={13} />],
                        ["audit", `Audit (${auditTrail.length})`, <IconScroll key="a" size={13} />],
                      ] as Array<[Tab, string, ReactNode]>
                    ).map(([tab, label, icon]) => (
                      <button
                        key={tab}
                        onClick={() => setActiveTab(tab)}
                        aria-current={activeTab === tab ? "page" : undefined}
                        className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition-all ${activeTab === tab
                          ? "bg-white text-slate-900 shadow-sm"
                          : "text-slate-500 hover:text-slate-900"
                          }`}
                      >
                        {icon}
                        {label}
                      </button>
                    ))}
                  </nav>
                </div>

                {/* Tab Content */}
                {activeTab === "investigation" && (
                  <div className="animate-fade">
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
                )}
                {activeTab === "evidence" && (
                  <div className="animate-fade">
                    <EvidenceChain evidence={caseDetail.case.evidence} />
                  </div>
                )}
                {activeTab === "audit" && (
                  <div className="animate-fade">
                    <AuditLog events={auditTrail} />
                  </div>
                )}
              </div>
            )}
          </main>
        </div>

        {/* ============================ Overlays ============================ */}
        {modalOpen && caseDetail && (
          <ApprovalModal
            detail={caseDetail}
            action={modalAction}
            busy={actionBusy}
            onClose={() => setModalOpen(false)}
            onConfirm={(rid, notes) => void confirmAuthority(rid, notes)}
          />
        )}
        <Toast toast={toast} onDismiss={() => setToast(null)} />
      </div>
    </div>
  );
}
