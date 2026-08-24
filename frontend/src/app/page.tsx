"use client";

/**
 * ARGUS CONTROL control room.
 *
 * Renders backend results only: no financial truth logic lives here and no
 * metric is displayed unless the API produced it. Loading, empty, partial
 * failure, and retry states are first-class (PRD §13.4).
 */

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type {
  AuditLogItem,
  CaseDetail,
  CaseSummary,
  ReconcileResponse,
  RunListItem,
} from "../lib/types";
import { formatCount, formatINR, formatRate, shortHash } from "../lib/format";
import { CaseRail, categoryMeta, StatusBadge } from "../components/case-rail";
import { CaseWorkspace } from "../components/case-workspace";
import { EvidenceChain } from "../components/evidence-chain";
import { AuditLog } from "../components/audit-log";
import { ApprovalModal } from "../components/approval-modal";
import {
  IconAperture,
  IconBolt,
  IconRefresh,
  IconRoute,
  IconScroll,
  IconShield,
} from "../components/icons";
import { Metric, Panel, Skeleton, Toast, type ToastState } from "../components/primitives";

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
    <span className="inline-flex items-center gap-2 rounded-full border border-white/[0.08] bg-black/40 px-3 py-1.5 text-[10px] font-medium text-zinc-400">
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${apiOk === false ? "bg-rose-400" : apiOk ? "bg-emerald-400 animate-pulse-dot" : "bg-zinc-500"}`} />
      {apiOk === false ? "API offline" : apiOk ? "API online" : "Connecting"}
    </span>
  );

  const syntheticPill = (
    <span className="hidden md:inline-flex items-center gap-2 rounded-full border border-white/[0.08] bg-black/40 px-3 py-1.5 text-[10px] font-medium text-zinc-500">
      <IconShield size={11} className="text-[#e6b45c]/80" />
      Tenant argus-demo · Synthetic data only
    </span>
  );

  return (
    <div className="app-shell flex h-screen flex-col overflow-hidden bg-[#08090b] text-zinc-200">
      <div className="app-backdrop flex h-full flex-col">
        {/* ============================ Top bar ============================ */}
        <header className="z-20 flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] px-5 py-3">
          <div className="flex min-w-0 items-center gap-3.5">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[#e6b45c]/25 bg-gradient-to-b from-[#e6b45c]/[0.14] to-transparent text-[#e6b45c] shadow-[0_0_28px_-8px_rgba(230,180,92,0.45)]">
              <IconAperture size={19} />
            </div>
            <div className="min-w-0">
              <h1 className="flex items-baseline gap-2 whitespace-nowrap font-serif text-[18px] font-bold italic leading-none tracking-tight text-zinc-50">
                Argus{" "}
                <span className="font-sans text-[10px] font-semibold not-italic tracking-[0.34em] text-zinc-500">
                  CONTROL
                </span>
              </h1>
              <p className="mt-1 hidden whitespace-nowrap text-[9px] font-medium uppercase tracking-[0.24em] text-zinc-600 sm:block">
                Financial flight recorder · reconciliation control room
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {syntheticPill}
            {apiPill}
            <button
              onClick={() => void triggerRun("dev", "rules-only")}
              disabled={running || booting}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.09] bg-white/[0.03] px-3.5 py-2 text-[11px] font-semibold text-zinc-200 transition-all hover:border-white/20 hover:bg-white/[0.06] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c] disabled:cursor-wait disabled:opacity-50"
            >
              {running ? (
                <span aria-hidden className="h-3 w-3 animate-spin rounded-full border-2 border-zinc-600 border-t-zinc-200" />
              ) : (
                <IconBolt size={13} className="text-[#e6b45c]" />
              )}
              {running ? "Reconciling…" : "Reconcile dev"}
            </button>
            <button
              onClick={() => void triggerRun("adversarial", "agent")}
              disabled={running || booting}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.09] bg-white/[0.03] px-3.5 py-2 text-[11px] font-semibold text-zinc-200 transition-all hover:border-white/20 hover:bg-white/[0.06] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c] disabled:cursor-wait disabled:opacity-50"
            >
              <IconRoute size={13} className="text-cyan-300" />
              AI adversarial batch
            </button>
          </div>
        </header>

        {/* ========================= Telemetry strip ======================== */}
        {booting && (
          <div className="grid shrink-0 grid-cols-2 gap-3 border-b border-white/[0.06] px-5 py-3 sm:grid-cols-3 xl:grid-cols-7">
            {Array.from({ length: 7 }).map((_, i) => (
              <Skeleton key={i} className="h-11" />
            ))}
          </div>
        )}

        {!booting && telemetry && (
          <div className="grid shrink-0 grid-cols-2 gap-x-5 gap-y-3 border-b border-white/[0.06] px-5 py-3.5 sm:grid-cols-3 xl:grid-cols-7">
            <Metric
              label="Active batch"
              value={shortHash(telemetry.runId, 18)}
              mono={false}
              sub={
                <span className="inline-flex items-center gap-1.5">
                  <span className={`rounded px-1 py-px font-mono text-[9px] uppercase tracking-wide ${telemetry.mode === "agent" ? "bg-cyan-400/10 text-cyan-300" : "bg-white/[0.06] text-zinc-400"}`}>
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
          <div className="shrink-0 border-b border-white/[0.06] px-5 py-4">
            <Panel className="mx-auto max-w-md p-5 text-center animate-rise">
              <p className="text-sm font-semibold text-zinc-200">No batches recorded yet</p>
              <p className="mt-1.5 text-xs leading-relaxed text-zinc-500">
                Run a reconciliation batch to populate the flight recorder. The
                deterministic pipeline works without any model key configured.
              </p>
              <div className="mt-4 flex justify-center gap-2.5">
                <button
                  onClick={() => void triggerRun("dev", "rules-only")}
                  disabled={running}
                  className="rounded-lg bg-gradient-to-b from-[#f0c878] to-[#d9a24a] px-4 py-2 text-xs font-bold text-black shadow-lg transition hover:from-[#f4cf88]"
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

          <main className="min-w-0 flex-1 overflow-y-auto">
            {!caseDetail ? (
              <div className="flex h-full items-center justify-center p-8">
                <div className="max-w-sm text-center">
                  <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-white/[0.07] bg-white/[0.03] text-zinc-500">
                    <IconScroll size={20} />
                  </div>
                  <p className="text-sm font-semibold text-zinc-300">No case file open</p>
                  <p className="mt-1.5 text-xs leading-relaxed text-zinc-600">
                    Select an exception from the queue to open its dossier,
                    hypotheses, deterministic proof, and audit trail.
                  </p>
                  {apiOk === false && (
                    <button
                      onClick={() => void loadRuns()}
                      className="mx-auto mt-4 inline-flex items-center gap-1.5 rounded-lg border border-white/[0.09] bg-white/[0.03] px-3.5 py-2 text-xs font-semibold text-zinc-200 transition hover:bg-white/[0.06]"
                    >
                      <IconRefresh size={13} /> Retry connection
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <div className="space-y-4 p-5">
                {/* Case header */}
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
                    <span className="select-all font-mono text-base font-bold tracking-tight text-[#ecd9ae]">
                      {caseDetail.case.case_id}
                    </span>
                    <span aria-hidden className="text-zinc-700">/</span>
                    <span
                      className="inline-flex items-center gap-1.5 text-xs font-semibold"
                      style={{ color: categoryMeta(caseDetail.case.category).hex }}
                    >
                      {categoryMeta(caseDetail.case.category).icon}
                      {categoryMeta(caseDetail.case.category).label}
                    </span>
                    <StatusBadge status={caseDetail.case.status} />
                  </div>

                  <nav aria-label="Workspace views" className="flex rounded-xl border border-white/[0.08] bg-black/40 p-1">
                    {(
                      [
                        ["investigation", "Investigation", <IconShield key="i" size={12} />],
                        ["evidence", "Evidence trace", <IconRoute key="e" size={12} />],
                        ["audit", `Audit (${auditTrail.length})`, <IconScroll key="a" size={12} />],
                      ] as Array<[Tab, string, ReactNode]>
                    ).map(([tab, label, icon]) => (
                      <button
                        key={tab}
                        onClick={() => setActiveTab(tab)}
                        aria-current={activeTab === tab ? "page" : undefined}
                        className={`inline-flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-[11px] font-semibold transition-colors focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c] ${
                          activeTab === tab
                            ? "bg-white/[0.08] text-zinc-100 shadow-inner"
                            : "text-zinc-500 hover:text-zinc-300"
                        }`}
                      >
                        {icon}
                        {label}
                      </button>
                    ))}
                  </nav>
                </div>

                {/* Tab content */}
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
