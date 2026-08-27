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
import Link from "next/link";
import type {
  AuditLogItem,
  CaseDetail,
  CaseSummary,
  ReconcileResponse,
  RunListItem,
} from "../../lib/types";
import { formatCount, formatINR, formatRate, shortHash } from "../../lib/format";
import { CaseRail, StatusBadge } from "../../components/case-rail";
import { CaseWorkspace } from "../../components/case-workspace";
import { EvidenceChain } from "../../components/evidence-chain";
import { AuditLog } from "../../components/audit-log";
import { ApprovalModal } from "../../components/approval-modal";
import { ConnectDatasetModal } from "../../components/connect-dataset-modal";
import { HomeChat } from "../../components/home-chat";
import {
  IconActivity,
  IconBolt,
  IconCheck,
  IconChevronDown,
  IconFlag,
  IconHome,
  IconLayers,
  IconPresentation,
  IconRoute,
  IconScale,
  IconScroll,
  IconShield,
  IconSidebar,
} from "../../components/icons";
import { Metric, Toast, type ToastState } from "../../components/primitives";
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
type Tab =
  | "home"
  | "approval_queue"
  | "verified_resolved"
  | "unresolved"
  | "dossier"
  | "evidence"
  | "ledger"
  | "audit";

export default function ControlRoomPage() {
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [caseDetail, setCaseDetail] = useState<CaseDetail | null>(null);
  const [auditTrail, setAuditTrail] = useState<AuditLogItem[]>([]);

  const [activeTab, setActiveTab] = useState<Tab>("home");
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
  const [connectDatasetOpen, setConnectDatasetOpen] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);

  useEffect(() => {
    window.dispatchEvent(new CustomEvent("argus-dashboard-tab", { detail: { tab: activeTab } }));
  }, [activeTab]);

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
        const err = (await res.json()) as { detail?: string };
        throw new Error(err.detail ?? `Reconciliation failed: ${res.status}`);
      }
      const data = (await res.json()) as ReconcileResponse;
      setToast({
        kind: "success",
        message: `Batch ${shortHash(data.run_id)} complete · status: ${data.status}`,
      });
      await loadRuns();
    } catch (e) {
      setToast({
        kind: "error",
        message: e instanceof Error ? e.message : "Run failed.",
      });
    } finally {
      setRunning(false);
    }
  }

  async function confirmAuthority(proofId: string, notes?: string) {
    if (!caseDetail) return;
    setActionBusy(true);
    try {
      const path = modalAction === "APPROVE" ? "approve" : "reject";
      const res = await fetch(`/api/v1/cases/${caseDetail.case.case_id}/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          proof_id: proofId,
          reviewer_id: "Merchant Controller (UI)",
          notes: notes ?? `${modalAction} authorized via dashboard`,
        }),
      });
      if (!res.ok) {
        const err = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(err.detail ?? "Action failed");
      }
      setToast({
        kind: "success",
        message: `Case ${caseDetail.case.case_id} ${modalAction === "APPROVE" ? "approved" : "rejected"} cleanly.`,
      });
      setModalOpen(false);
      await selectCase(caseDetail.case.case_id);
      if (activeRunId) await loadCases(activeRunId);
    } catch (e) {
      setToast({
        kind: "error",
        message: e instanceof Error ? e.message : "Authority submission failed",
      });
    } finally {
      setActionBusy(false);
    }
  }

  /* ----------------------------- derived -------------------------- */

  const activeRun = runs.find((r) => r.run_id === activeRunId) ?? runs[0];
  const telemetry = activeRun ? telemetryFromRun(activeRun) : undefined;

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
                  onClick={() => {
                    setActiveTab("dossier");
                    setStatusFilter("ALL");
                    void triggerRun("dev", "rules-only");
                  }}
                  disabled={running || booting}
                  title="Reconcile Dev"
                  className="group flex h-10 w-full items-center rounded-xl text-slate-700 hover:bg-slate-100/80 hover:text-slate-900 transition-all disabled:opacity-50 overflow-hidden"
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconBolt size={17} />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px] font-medium">Reconcile Dev</span>
                  </div>
                </button>

                <button
                  onClick={() => {
                    setActiveTab("dossier");
                    setStatusFilter("ALL");
                    void triggerRun("adversarial", "agent");
                  }}
                  disabled={running || booting}
                  title="AI Adversarial"
                  className="group flex h-10 w-full items-center rounded-xl text-slate-700 hover:bg-slate-100/80 hover:text-slate-900 transition-all disabled:opacity-50 overflow-hidden"
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                    <IconRoute size={17} />
                  </div>
                  <div
                    className={`flex items-center overflow-hidden whitespace-nowrap transition-all duration-200 ${sidebarOpen ? "max-w-[170px] opacity-100 pr-2" : "max-w-0 opacity-0 pointer-events-none pr-0"
                      }`}
                  >
                    <span className="truncate text-[13px] font-medium">AI Adversarial</span>
                  </div>
                </button>

                <button
                  onClick={() => {
                    setCategoryFilter("ALL");
                    setSearchQuery("");
                    setStatusFilter(CaseStatus.APPROVAL_REQUIRED);
                    setActiveTab("approval_queue");
                    const match = cases.find((c) => c.status === CaseStatus.APPROVAL_REQUIRED);
                    if (match) void selectCase(match.case_id);
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
                    setStatusFilter(CaseStatus.VERIFIED_RESOLVED);
                    setActiveTab("verified_resolved");
                    const match = cases.find((c) => c.status === CaseStatus.VERIFIED_RESOLVED);
                    if (match) void selectCase(match.case_id);
                  }}
                  title="Verified Resolved"
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
                    if (match) void selectCase(match.case_id);
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
              <IconActivity size={17} />
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
          <div className="flex items-center gap-3">
            <h1 className="text-sm font-semibold tracking-tight text-slate-900">
              Argus Control <span className="font-normal text-slate-400">· Financial Flight Recorder</span>
            </h1>
          </div>

          <div className="flex items-center gap-3">
            <span className="hidden items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-700 sm:inline-flex">
              <span className="h-1.5 w-1.5 rounded-full bg-blue-500" />
              Tenant argus-demo · Synthetic data only
            </span>
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${apiOk === true
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                  : apiOk === false
                    ? "border-rose-200 bg-rose-50 text-rose-800"
                    : "border-slate-200 bg-slate-50 text-slate-600"
                }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${apiOk === true
                    ? "bg-emerald-500 animate-pulse-dot"
                    : apiOk === false
                      ? "bg-rose-500"
                      : "bg-slate-400"
                  }`}
              />
              {apiOk === true ? "API connected" : apiOk === false ? "API offline" : "Checking API…"}
            </span>
          </div>
        </header>

        {/* ============================ Distinct Dedicated Views ============================ */}
        {activeTab === "home" && (
          <div className="flex flex-1 flex-col overflow-hidden bg-slate-50/40 p-4 sm:p-6">
            <HomeChat
              onTriggerRun={triggerRun}
              onOpenConnectModal={() => setConnectDatasetOpen(true)}
              telemetry={telemetry}
            />
          </div>
        )}

        {/* ============================ Approval Queue View ============================ */}
        {activeTab === "approval_queue" && (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-amber-200/70 bg-amber-50/60 px-6 py-3 shrink-0">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-amber-100 text-amber-700 shadow-2xs">
                  <IconShield size={17} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-bold text-slate-900">Human Approval Queue</h2>
                    <span className="rounded-full bg-amber-200/80 px-2 py-0.5 text-[11px] font-bold text-amber-900">
                      {cases.filter((c) => c.status === CaseStatus.APPROVAL_REQUIRED).length} Pending Sign-off
                    </span>
                  </div>
                  <p className="text-xs text-slate-600">
                    Zero financial corrections apply without explicit human authorization. Review dry-run deltas before granting sign-off.
                  </p>
                </div>
              </div>
            </div>

            <div className="flex min-h-0 flex-1">
              <CaseRail
                cases={cases.filter((c) => c.status === CaseStatus.APPROVAL_REQUIRED)}
                loading={booting}
                selectedCaseId={selectedCaseId}
                onSelect={(id) => void selectCase(id)}
                statusFilter={CaseStatus.APPROVAL_REQUIRED}
                categoryFilter={categoryFilter}
                onCategoryFilter={setCategoryFilter}
                searchQuery={searchQuery}
                onSearchQuery={setSearchQuery}
                title="Approval Cases"
                hideStatusFilters={true}
              />
              <main className="min-w-0 flex-1 overflow-y-auto bg-[#f8fafc] p-6">
                {caseDetail && caseDetail.case.status === CaseStatus.APPROVAL_REQUIRED ? (
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
                  <div className="flex h-full items-center justify-center text-center p-8">
                    <div className="max-w-sm">
                      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200 bg-white text-amber-500 shadow-sm">
                        <IconShield size={22} />
                      </div>
                      <p className="text-sm font-bold text-slate-800">No Pending Approvals</p>
                      <p className="mt-1 text-xs text-slate-500">
                        All cases requiring human approval have been authorized or rejected.
                      </p>
                    </div>
                  </div>
                )}
              </main>
            </div>
          </div>
        )}

        {/* ============================ Verified Resolved View ============================ */}
        {activeTab === "verified_resolved" && (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-emerald-200/70 bg-emerald-50/60 px-6 py-3 shrink-0">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-emerald-100 text-emerald-700 shadow-2xs">
                  <IconCheck size={17} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-bold text-slate-900">Verified Resolutions</h2>
                    <span className="rounded-full bg-emerald-200/80 px-2 py-0.5 text-[11px] font-bold text-emerald-900">
                      {cases.filter((c) => c.status === CaseStatus.VERIFIED_RESOLVED).length} Verified Closed
                    </span>
                  </div>
                  <p className="text-xs text-slate-600">
                    Exceptions closed with 100% deterministic verifier PASS, cited rule versions, and evidence hashes.
                  </p>
                </div>
              </div>
            </div>

            <div className="flex min-h-0 flex-1">
              <CaseRail
                cases={cases.filter((c) => c.status === CaseStatus.VERIFIED_RESOLVED)}
                loading={booting}
                selectedCaseId={selectedCaseId}
                onSelect={(id) => void selectCase(id)}
                statusFilter={CaseStatus.VERIFIED_RESOLVED}
                categoryFilter={categoryFilter}
                onCategoryFilter={setCategoryFilter}
                searchQuery={searchQuery}
                onSearchQuery={setSearchQuery}
                title="Verified Cases"
                hideStatusFilters={true}
              />
              <main className="min-w-0 flex-1 overflow-y-auto bg-[#f8fafc] p-6">
                {caseDetail && caseDetail.case.status === CaseStatus.VERIFIED_RESOLVED ? (
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
                  <div className="flex h-full items-center justify-center text-center p-8">
                    <div className="max-w-sm">
                      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200 bg-white text-emerald-500 shadow-sm">
                        <IconCheck size={22} />
                      </div>
                      <p className="text-sm font-bold text-slate-800">No Verified Cases in View</p>
                      <p className="mt-1 text-xs text-slate-500">
                        Run a reconciliation batch to inspect verified exceptions.
                      </p>
                    </div>
                  </div>
                )}
              </main>
            </div>
          </div>
        )}

        {/* ============================ Unresolved Cases View ============================ */}
        {activeTab === "unresolved" && (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-rose-200/70 bg-rose-50/60 px-6 py-3 shrink-0">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-rose-100 text-rose-700 shadow-2xs">
                  <IconFlag size={17} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-bold text-slate-900">Unresolved Exceptions & Ambiguities</h2>
                    <span className="rounded-full bg-rose-200/80 px-2 py-0.5 text-[11px] font-bold text-rose-900">
                      {cases.filter((c) => c.status === CaseStatus.UNRESOLVED).length} Unresolved
                    </span>
                  </div>
                  <p className="text-xs text-slate-600">
                    Cases left deliberately unresolved due to inconclusive evidence. Ambiguity cannot be overridden by AI confidence.
                  </p>
                </div>
              </div>
            </div>

            <div className="flex min-h-0 flex-1">
              <CaseRail
                cases={cases.filter((c) => c.status === CaseStatus.UNRESOLVED)}
                loading={booting}
                selectedCaseId={selectedCaseId}
                onSelect={(id) => void selectCase(id)}
                statusFilter={CaseStatus.UNRESOLVED}
                categoryFilter={categoryFilter}
                onCategoryFilter={setCategoryFilter}
                searchQuery={searchQuery}
                onSearchQuery={setSearchQuery}
                title="Unresolved Cases"
                hideStatusFilters={true}
              />
              <main className="min-w-0 flex-1 overflow-y-auto bg-[#f8fafc] p-6">
                {caseDetail && caseDetail.case.status === CaseStatus.UNRESOLVED ? (
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
                  <div className="flex h-full items-center justify-center text-center p-8">
                    <div className="max-w-sm">
                      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200 bg-white text-rose-500 shadow-sm">
                        <IconFlag size={22} />
                      </div>
                      <p className="text-sm font-bold text-slate-800">No Unresolved Cases in View</p>
                      <p className="mt-1 text-xs text-slate-500">
                        All exceptions in the current batch have verified or completed.
                      </p>
                    </div>
                  </div>
                )}
              </main>
            </div>
          </div>
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

            <div className="flex min-h-0 flex-1">
              <CaseRail
                cases={cases}
                loading={booting}
                selectedCaseId={selectedCaseId}
                onSelect={(id) => void selectCase(id)}
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
                {caseDetail ? (
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
                  <div className="flex h-full items-center justify-center text-center p-8">
                    <div className="max-w-sm">
                      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-400 shadow-sm">
                        <IconScroll size={22} />
                      </div>
                      <p className="text-sm font-bold text-slate-800">No case file open</p>
                      <p className="mt-1.5 text-xs leading-relaxed text-slate-500">
                        Select an exception from the queue to open its dossier, hypotheses, and deterministic proof.
                      </p>
                    </div>
                  </div>
                )}
              </main>
            </div>
          </div>
        )}

        {/* ============================ Evidence Trace View ============================ */}
        {activeTab === "evidence" && (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-indigo-200/70 bg-indigo-50/60 px-6 py-3 shrink-0">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-indigo-100 text-indigo-700 shadow-2xs">
                  <IconRoute size={17} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-bold text-slate-900">Interactive Evidence Trace Graph</h2>
                    {caseDetail && (
                      <span className="font-mono text-xs font-bold text-indigo-900 bg-indigo-100 px-2 py-0.5 rounded">
                        {caseDetail.case.case_id}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-600">
                    Cryptographically linked chain of Gateway Events, Settlement Batches, Bank Feeds, and Rule Validations.
                  </p>
                </div>
              </div>
            </div>

            <div className="flex min-h-0 flex-1">
              <CaseRail
                cases={cases}
                loading={booting}
                selectedCaseId={selectedCaseId}
                onSelect={(id) => void selectCase(id)}
                statusFilter="ALL"
                categoryFilter={categoryFilter}
                onCategoryFilter={setCategoryFilter}
                searchQuery={searchQuery}
                onSearchQuery={setSearchQuery}
                title="Trace Selector"
                hideStatusFilters={true}
              />
              <main className="min-w-0 flex-1 overflow-y-auto bg-[#f8fafc] p-6">
                {caseDetail ? (
                  <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                    <div className="mb-4 flex items-center justify-between border-b border-slate-100 pb-3">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-sm font-bold text-slate-900">{caseDetail.case.case_id}</span>
                        <StatusBadge status={caseDetail.case.status} />
                      </div>
                      <span className="text-xs text-slate-500 font-medium">
                        {caseDetail.case.evidence.length} Cited Evidence Artifacts
                      </span>
                    </div>
                    <EvidenceChain evidence={caseDetail.case.evidence} />
                  </div>
                ) : (
                  <div className="flex h-full items-center justify-center text-center p-8">
                    <div className="max-w-sm">
                      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200 bg-white text-indigo-500 shadow-sm">
                        <IconRoute size={22} />
                      </div>
                      <p className="text-sm font-bold text-slate-800">Select a Case to Trace Evidence</p>
                      <p className="mt-1 text-xs text-slate-500">
                        Choose an exception from the list to visualize its cryptographic evidence nodes.
                      </p>
                    </div>
                  </div>
                )}
              </main>
            </div>
          </div>
        )}

        {/* ============================ Ledger Dry-Run View ============================ */}
        {activeTab === "ledger" && (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-teal-200/70 bg-teal-50/60 px-6 py-3 shrink-0">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-teal-100 text-teal-700 shadow-2xs">
                  <IconScale size={17} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-bold text-slate-900">Ledger Simulation & Dry-Run Inspector</h2>
                    {caseDetail && (
                      <span className="font-mono text-xs font-bold text-teal-900 bg-teal-100 px-2 py-0.5 rounded">
                        {caseDetail.case.case_id}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-600">
                    Pre-calculated ledger deltas in signed integer paise. Live ledger entries are never modified directly.
                  </p>
                </div>
              </div>
            </div>

            <div className="flex min-h-0 flex-1">
              <CaseRail
                cases={cases}
                loading={booting}
                selectedCaseId={selectedCaseId}
                onSelect={(id) => void selectCase(id)}
                statusFilter="ALL"
                categoryFilter={categoryFilter}
                onCategoryFilter={setCategoryFilter}
                searchQuery={searchQuery}
                onSearchQuery={setSearchQuery}
                title="Ledger Cases"
                hideStatusFilters={true}
              />
              <main className="min-w-0 flex-1 overflow-y-auto bg-[#f8fafc] p-6">
                {caseDetail?.dry_run ? (
                  <div className="space-y-6">
                    <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                      <h3 className="text-sm font-bold text-slate-900 mb-2">Simulated Correction Proposal</h3>
                      <p className="text-xs text-slate-600 mb-4">{caseDetail.proof?.claim ?? "Deterministic ledger dry-run simulation"}</p>

                      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 border-t border-slate-100 pt-4">
                        <div className="rounded-xl bg-slate-50 p-3">
                          <span className="text-[11px] font-semibold text-slate-500 uppercase">Adjustment Delta</span>
                          <p className="text-base font-bold text-slate-900 font-mono mt-1">
                            {formatINR(caseDetail.dry_run.proposed_delta_paise)}
                          </p>
                        </div>
                        <div className="rounded-xl bg-slate-50 p-3">
                          <span className="text-[11px] font-semibold text-slate-500 uppercase">Target Ledger Entry</span>
                          <p className="text-xs font-mono font-bold text-slate-800 mt-1">
                            {caseDetail.dry_run.target_ledger_entry_id ?? "New Simulated Correction"}
                          </p>
                        </div>
                        <div className="rounded-xl bg-slate-50 p-3">
                          <span className="text-[11px] font-semibold text-slate-500 uppercase">Target Account</span>
                          <p className="text-xs font-mono font-bold text-slate-800 mt-1">
                            {caseDetail.dry_run.account_code ?? "DEFAULT_SETTLEMENT"}
                          </p>
                        </div>
                      </div>

                      {caseDetail.case.status === CaseStatus.APPROVAL_REQUIRED && caseDetail.proof && (
                        <div className="mt-6 flex justify-end gap-3 border-t border-slate-100 pt-4">
                          <button
                            onClick={() => {
                              setModalAction("REJECT");
                              setModalOpen(true);
                            }}
                            className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50 transition-colors"
                          >
                            Reject Proposal
                          </button>
                          <button
                            onClick={() => {
                              setModalAction("APPROVE");
                              setModalOpen(true);
                            }}
                            className="rounded-lg bg-emerald-600 px-4 py-2 text-xs font-bold text-white hover:bg-emerald-700 shadow-xs transition-colors"
                          >
                            Authorize Simulated Correction
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="flex h-full items-center justify-center text-center p-8">
                    <div className="max-w-sm">
                      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-slate-200 bg-white text-teal-500 shadow-sm">
                        <IconScale size={22} />
                      </div>
                      <p className="text-sm font-bold text-slate-800">No Ledger Correction Required</p>
                      <p className="mt-1 text-xs text-slate-500">
                        This case does not have a proposed ledger adjustment or has completed.
                      </p>
                    </div>
                  </div>
                )}
              </main>
            </div>
          </div>
        )}

        {/* ============================ Audit Trail View ============================ */}
        {activeTab === "audit" && (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-cyan-200/70 bg-cyan-50/60 px-6 py-3 shrink-0">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-cyan-100 text-cyan-700 shadow-2xs">
                  <IconScroll size={17} />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-bold text-slate-900">Cryptographic Flight Recorder Log</h2>
                    <span className="rounded-full bg-cyan-200/80 px-2 py-0.5 text-[11px] font-bold text-cyan-900">
                      {auditTrail.length} Append-Only Events
                    </span>
                  </div>
                  <p className="text-xs text-slate-600">
                    Immutable sequence of verification passes, AI hypotheses, and human approval signatures.
                  </p>
                </div>
              </div>
            </div>

            <div className="flex min-h-0 flex-1">
              <CaseRail
                cases={cases}
                loading={booting}
                selectedCaseId={selectedCaseId}
                onSelect={(id) => void selectCase(id)}
                statusFilter="ALL"
                categoryFilter={categoryFilter}
                onCategoryFilter={setCategoryFilter}
                searchQuery={searchQuery}
                onSearchQuery={setSearchQuery}
                title="Audit Selector"
                hideStatusFilters={true}
              />
              <main className="min-w-0 flex-1 overflow-y-auto bg-[#f8fafc] p-6">
                <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                  <div className="mb-4 flex items-center justify-between border-b border-slate-100 pb-3">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-slate-900">Case Audit Trail</span>
                      {caseDetail && <span className="font-mono text-xs text-slate-500 font-semibold">{caseDetail.case.case_id}</span>}
                    </div>
                    {telemetry?.econHash && (
                      <span className="font-mono text-[11px] text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded">
                        SHA-256: {shortHash(telemetry.econHash, 14)}
                      </span>
                    )}
                  </div>
                  <AuditLog events={auditTrail} />
                </div>
              </main>
            </div>
          </div>
        )}

        {/* ============================ Overlays ============================ */}
        <ConnectDatasetModal
          open={connectDatasetOpen}
          onClose={() => setConnectDatasetOpen(false)}
          onRunSynthetic={(profile, mode) => {
            setActiveTab("dossier");
            setStatusFilter("ALL");
            void triggerRun(profile, mode);
          }}
          onSyncSuccess={(runId) => {
            setActiveRunId(runId);
            setActiveTab("dossier");
            setStatusFilter("ALL");
            void loadRuns();
            void loadCases(runId);
            setToast({
              message: `Synced and reconciled live Razorpay data (Run ${runId.slice(0, 10)})`,
              kind: "success",
            });
          }}
        />
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
