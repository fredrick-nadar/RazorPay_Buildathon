"use client";

import React, { useCallback, useEffect, useState } from "react";
import { CaseStatus } from "../domain/enums";

interface CaseSummary {
  case_id: string;
  run_id: string;
  category: string;
  status: string;
  variance_paise: number;
  affected_amount_paise: number;
  proposed_delta_paise: number | null;
  currency: string;
  summary: string;
  reason_codes: string[];
  evidence: Array<{ record_type: string; record_id: string; note: string | null }>;
  opened_at_utc: string;
  updated_at_utc: string;
}

interface CaseDetail {
  case: CaseSummary;
  hypotheses: Array<{
    hypothesis_id: string;
    category: string;
    claim: string;
    evidence: string[];
    status: string;
    reason_codes: string[];
    created_at_utc: string;
  }>;
  proof: {
    proof_id: string;
    hypothesis_id: string;
    claim: string;
    category: string;
    evidence: string[];
    supported_evidence: string[];
    conflicting_evidence: string[];
    equations: string[];
    rejected_alternatives: string[];
    verifier_status: string;
    verifier_rule_id: string;
    verifier_rule_version: string;
    proposed_delta_paise: number | null;
    authority_decision: string;
    requires_approval: boolean;
    uncertainty: string[];
    competing_candidates: string[];
    canonical_hash: string;
    created_at_utc: string;
  } | null;
  dry_run: {
    correction_id: string;
    proof_id: string;
    status: string;
    proposed_entry: Record<string, unknown> | null;
    target_ledger_entry_id: string | null;
    account_code: string | null;
    proposed_delta_paise: number;
    variance_before_paise: number;
    variance_after_paise: number;
    totals_before_paise: Record<string, number>;
    totals_after_paise: Record<string, number>;
    warnings: string[];
    uncertainty: string[];
    created_at_utc: string;
  } | null;
  simulated_correction: {
    correction_id: string;
    case_id: string;
    run_id: string;
    proof_id: string;
    approval_id: string;
    target_ledger_entry_id: string | null;
    account_code: string;
    delta_paise: number;
    applied_at_utc: string;
    idempotency_key: string;
  } | null;
  approvals: Array<{
    approval_id: string;
    proof_id: string;
    reviewer_id: string;
    action: string;
    notes: string | null;
    approved_at_utc: string;
  }>;
}

interface AuditLogItem {
  event_id: string;
  case_id: string | null;
  run_id: string | null;
  timestamp_utc: string;
  actor: string;
  action: string;
  payload: Record<string, unknown>;
  digest: string;
}

interface RunSummary {
  run_id: string;
  status: string;
  summary: {
    eligible_record_count: number;
    matched_record_count: number;
    cases_count: number;
    cases_by_category: Record<string, number>;
    financial_control_totals: Record<string, number>;
    economic_output_hash: string;
  };
}

function formatINR(paise: number): string {
  const rupees = paise / 100;
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
  }).format(rupees);
}

export default function ControlRoomPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [caseDetail, setCaseDetail] = useState<CaseDetail | null>(null);
  const [auditTrail, setAuditTrail] = useState<AuditLogItem[]>([]);
  const [activeTab, setActiveTab] = useState<"workspace" | "graph" | "audit">("workspace");
  const [graphViewMode, setGraphViewMode] = useState<"visual" | "table">("visual");

  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [categoryFilter, setCategoryFilter] = useState<string>("ALL");
  const [loading, setLoading] = useState<boolean>(false);
  const [actionLoading, setActionLoading] = useState<boolean>(false);
  const [reviewerNotes, setReviewerNotes] = useState<string>("");
  const [approvalModalOpen, setApprovalModalOpen] = useState<boolean>(false);
  const [modalAction, setModalAction] = useState<"APPROVE" | "REJECT">("APPROVE");

  const selectCase = useCallback(async (caseId: string) => {
    setSelectedCaseId(caseId);
    try {
      const res = await fetch(`/api/v1/cases/${caseId}`);
      if (res.ok) {
        const detail = (await res.json()) as CaseDetail;
        setCaseDetail(detail);
      }
      const auditRes = await fetch(`/api/v1/cases/${caseId}/audit`);
      if (auditRes.ok) {
        const audit = (await auditRes.json()) as AuditLogItem[];
        setAuditTrail(audit);
      }
    } catch (e) {
      console.error(e);
    }
  }, []);

  const fetchCases = useCallback(
    async (runId: string) => {
      try {
        const res = await fetch(`/api/v1/runs/${runId}/cases`);
        if (res.ok) {
          const data: CaseSummary[] = (await res.json()) as CaseSummary[];
          setCases(data);
          if (data.length > 0 && data[0]) {
            void selectCase(data[0].case_id);
          } else {
            setSelectedCaseId(null);
            setCaseDetail(null);
          }
        }
      } catch (e) {
        console.error(e);
      }
    },
    [selectCase],
  );

  const fetchRuns = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch("/api/v1/runs");
      if (res.ok) {
        const data = (await res.json()) as RunSummary[];
        setRuns(data);
        if (data.length > 0 && data[0]) {
          const firstRun = data[0].run_id;
          setActiveRunId(firstRun);
          void fetchCases(firstRun);
        }
      }

    } catch {
      // offline fallback
    } finally {
      setLoading(false);
    }
  }, [fetchCases]);

  useEffect(() => {
    void fetchRuns();
  }, [fetchRuns]);

  async function handleTriggerRun(profile: string = "dev", mode: string = "rules-only") {
    try {
      setLoading(true);
      const res = await fetch("/api/v1/runs/reconcile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dataset_profile: profile, mode, force: true }),
      });
      if (res.ok) {
        const run = (await res.json()) as { run_id: string };
        setActiveRunId(run.run_id);
        await fetchRuns();
        await fetchCases(run.run_id);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  async function handleApproveOrReject() {
    if (!selectedCaseId) return;
    try {
      setActionLoading(true);
      const endpoint = modalAction === "APPROVE" ? "approve" : "reject";
      const res = await fetch(`/api/v1/cases/${selectedCaseId}/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reviewer_id: "reviewer-finance-controller",
          notes:
            reviewerNotes ||
            (modalAction === "APPROVE" ? "Approved by controller" : "Rejected by controller"),
        }),
      });
      if (res.ok) {
        setApprovalModalOpen(false);
        setReviewerNotes("");
        if (activeRunId) {
          await fetchCases(activeRunId);
        }
        await selectCase(selectedCaseId);
      } else {
        const err = (await res.json()) as { detail?: string };
        alert(`Action failed: ${err.detail || "Unknown error"}`);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      alert(`Error: ${msg}`);
    } finally {
      setActionLoading(false);
    }
  }

  const filteredCases = cases.filter((c) => {
    if (statusFilter !== "ALL" && c.status !== statusFilter) return false;
    if (categoryFilter !== "ALL" && c.category !== categoryFilter) return false;
    return true;
  });

  const activeRun = runs.find((r) => r.run_id === activeRunId);

  return (
    <div className="flex h-screen flex-col bg-slate-950 text-slate-100 antialiased overflow-hidden font-sans">
      {/* Top Header */}
      <header className="border-b border-slate-800/80 bg-slate-900/60 px-6 py-3 backdrop-blur-md flex items-center justify-between z-10 shrink-0">
        <div className="flex items-center gap-4">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-bold text-lg shadow-sm shadow-emerald-500/10">
            A
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-bold tracking-tight text-white">ARGUS CONTROL</h1>
              <span className="rounded bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 text-[11px] font-semibold text-emerald-400">
                Phase 5 Control Room
              </span>
            </div>
            <p className="text-xs text-slate-400">
              Financial Flight Recorder for Merchant Reconciliation | Track 04
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-400 hidden md:inline">
            This prototype uses synthetic data only. It never moves real money.
          </span>
          <button
            onClick={() => void handleTriggerRun("dev", "rules-only")}
            disabled={loading}
            className="rounded-md bg-slate-800 hover:bg-slate-700 border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-200 transition-colors disabled:opacity-50"
          >
            Reconcile Dev
          </button>
          <button
            onClick={() => void handleTriggerRun("adversarial", "agent")}
            disabled={loading}
            className="rounded-md bg-emerald-600 hover:bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white shadow-sm shadow-emerald-600/30 transition-colors disabled:opacity-50"
          >
            Reconcile Adversarial (AI)
          </button>
        </div>
      </header>

      {/* Main KPI / Run Summary Banner */}
      {activeRun && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3 px-6 py-3 border-b border-slate-800/60 bg-slate-900/30 text-xs shrink-0">
          <div className="p-2 rounded bg-slate-900/60 border border-slate-800">
            <span className="text-slate-400 block text-[10px] uppercase font-semibold">Active Run</span>
            <span className="font-mono text-slate-200 truncate block">{activeRun.run_id}</span>
          </div>
          <div className="p-2 rounded bg-slate-900/60 border border-slate-800">
            <span className="text-slate-400 block text-[10px] uppercase font-semibold">Eligible Records</span>
            <span className="font-semibold text-slate-100 text-sm">{activeRun.summary?.eligible_record_count ?? 0}</span>
          </div>
          <div className="p-2 rounded bg-slate-900/60 border border-slate-800">
            <span className="text-slate-400 block text-[10px] uppercase font-semibold">Matched Clean</span>
            <span className="font-semibold text-emerald-400 text-sm">{activeRun.summary?.matched_record_count ?? 0}</span>
          </div>
          <div className="p-2 rounded bg-slate-900/60 border border-slate-800">
            <span className="text-slate-400 block text-[10px] uppercase font-semibold">Exceptions Created</span>
            <span className="font-semibold text-amber-400 text-sm">{activeRun.summary?.cases_count ?? cases.length}</span>
          </div>
          <div className="p-2 rounded bg-slate-900/60 border border-slate-800">
            <span className="text-slate-400 block text-[10px] uppercase font-semibold">Batch Status</span>
            <span className="font-semibold text-emerald-400 uppercase text-xs">{activeRun.status}</span>
          </div>
          <div className="p-2 rounded bg-slate-900/60 border border-slate-800">
            <span className="text-slate-400 block text-[10px] uppercase font-semibold">Gross Volume</span>
            <span className="font-semibold text-slate-200 text-xs">
              {formatINR(activeRun.summary?.financial_control_totals?.gross_amount_paise ?? 0)}
            </span>
          </div>
        </div>
      )}

      {/* Main Workspace Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left Column: Exception Queue */}
        <aside className="w-80 md:w-96 border-r border-slate-800/80 bg-slate-900/20 flex flex-col shrink-0">
          <div className="p-3 border-b border-slate-800/60 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-slate-300 uppercase tracking-wider">
                Exception Cases ({filteredCases.length})
              </span>
              <select
                value={categoryFilter}
                onChange={(e) => setCategoryFilter(e.target.value)}
                className="bg-slate-950 border border-slate-800 rounded px-1.5 py-0.5 text-[10px] text-slate-300 focus:outline-none"
              >
                <option value="ALL">All Categories</option>
                <option value="DUPLICATE_LEDGER_POSTING">Duplicate Ledger</option>
                <option value="MISSING_REFUND_POSTING">Missing Refund</option>
                <option value="SETTLEMENT_TIMING_WINDOW_SHIFT">Timing Shift</option>
                <option value="AMBIGUOUS_EVIDENCE">Ambiguous</option>
              </select>
            </div>
            {/* Filter pills */}
            <div className="flex gap-1 overflow-x-auto text-[11px] pb-1">
              {["ALL", CaseStatus.APPROVAL_REQUIRED, CaseStatus.SIMULATED_APPLIED, CaseStatus.UNRESOLVED].map((st) => (
                <button
                  key={st}
                  onClick={() => setStatusFilter(st)}
                  className={`px-2 py-1 rounded transition-colors whitespace-nowrap ${
                    statusFilter === st
                      ? "bg-slate-700 text-white font-medium"
                      : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
                  }`}
                >
                  {st === "ALL" ? "All" : st.replace("_", " ")}
                </button>
              ))}
            </div>
          </div>

          {/* Case List */}
          <div className="flex-1 overflow-y-auto divide-y divide-slate-800/50">
            {filteredCases.map((c) => {
              const isSelected = c.case_id === selectedCaseId;
              let statusBadgeBg = "bg-slate-800 text-slate-300 border-slate-700";
              if (c.status === CaseStatus.APPROVAL_REQUIRED) {
                statusBadgeBg = "bg-amber-500/10 text-amber-400 border-amber-500/30";
              } else if (c.status === CaseStatus.SIMULATED_APPLIED) {
                statusBadgeBg = "bg-blue-500/10 text-blue-400 border-blue-500/30";
              } else if (c.status === CaseStatus.VERIFIED_RESOLVED) {
                statusBadgeBg = "bg-emerald-500/10 text-emerald-400 border-emerald-500/30";
              } else if (c.status === CaseStatus.UNRESOLVED) {
                statusBadgeBg = "bg-violet-500/10 text-violet-400 border-violet-500/30";
              }

              return (
                <button
                  key={c.case_id}
                  onClick={() => void selectCase(c.case_id)}
                  className={`w-full text-left p-3.5 transition-all block ${
                    isSelected
                      ? "bg-emerald-950/20 border-l-2 border-emerald-500 shadow-inner"
                      : "hover:bg-slate-900/60"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="font-mono text-xs font-semibold text-slate-200">{c.case_id}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium uppercase ${statusBadgeBg}`}>
                      {c.status.replace("_", " ")}
                    </span>
                  </div>
                  <div className="text-xs text-slate-300 font-medium truncate mb-1">
                    {c.category.replace(/_/g, " ")}
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-slate-400">
                    <span>Variance: <strong className="text-slate-200">{formatINR(c.variance_paise)}</strong></span>
                    {c.proposed_delta_paise !== null && (
                      <span>Delta: <strong className="text-emerald-400">{formatINR(c.proposed_delta_paise)}</strong></span>
                    )}
                  </div>
                </button>
              );
            })}

            {filteredCases.length === 0 && (
              <div className="p-8 text-center text-xs text-slate-500">
                No exception cases found for this filter.
              </div>
            )}
          </div>
        </aside>

        {/* Right Column: Case Workspace & Evidence Graph */}
        <main className="flex-1 flex flex-col bg-slate-950 overflow-hidden">
          {caseDetail ? (
            <>
              {/* Workspace Navigation Bar */}
              <div className="border-b border-slate-800/80 bg-slate-900/40 px-6 py-2.5 flex items-center justify-between shrink-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm font-bold text-emerald-400">{caseDetail.case.case_id}</span>
                  <span className="text-slate-600">/</span>
                  <span className="text-xs font-medium text-slate-300">{caseDetail.case.category}</span>
                </div>

                <div className="flex items-center gap-1 bg-slate-900 p-1 rounded-md border border-slate-800">
                  <button
                    onClick={() => setActiveTab("workspace")}
                    className={`px-3 py-1 text-xs rounded font-medium transition-colors ${
                      activeTab === "workspace" ? "bg-slate-800 text-white" : "text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    Investigation & Proof
                  </button>
                  <button
                    onClick={() => setActiveTab("graph")}
                    className={`px-3 py-1 text-xs rounded font-medium transition-colors ${
                      activeTab === "graph" ? "bg-slate-800 text-white" : "text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    Evidence Graph
                  </button>
                  <button
                    onClick={() => setActiveTab("audit")}
                    className={`px-3 py-1 text-xs rounded font-medium transition-colors ${
                      activeTab === "audit" ? "bg-slate-800 text-white" : "text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    Audit Trail ({auditTrail.length})
                  </button>
                </div>
              </div>

              {/* Workspace Content */}
              <div className="flex-1 overflow-y-auto p-6 space-y-6">
                {activeTab === "workspace" && (
                  <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    {/* Left 2 Cols: Details, Hypotheses, Proof, Dry-Run */}
                    <div className="lg:col-span-2 space-y-6">
                      {/* Case Overview Box */}
                      <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-5 space-y-3">
                        <h2 className="text-sm font-bold text-slate-200 uppercase tracking-wider">Case Overview</h2>
                        <p className="text-sm text-slate-300 leading-relaxed">{caseDetail.case.summary}</p>
                        <div className="grid grid-cols-3 gap-3 pt-2 text-xs">
                          <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80">
                            <span className="text-slate-400 block text-[10px] uppercase">Variance</span>
                            <span className="text-sm font-bold text-amber-400">{formatINR(caseDetail.case.variance_paise)}</span>
                          </div>
                          <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80">
                            <span className="text-slate-400 block text-[10px] uppercase">Affected Amount</span>
                            <span className="text-sm font-bold text-slate-200">{formatINR(caseDetail.case.affected_amount_paise)}</span>
                          </div>
                          <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80">
                            <span className="text-slate-400 block text-[10px] uppercase">Proposed Delta</span>
                            <span className="text-sm font-bold text-emerald-400">
                              {caseDetail.case.proposed_delta_paise !== null ? formatINR(caseDetail.case.proposed_delta_paise) : "None"}
                            </span>
                          </div>
                        </div>
                      </div>

                      {/* Hypotheses & Evidence */}
                      <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-5 space-y-4">
                        <h2 className="text-sm font-bold text-slate-200 uppercase tracking-wider">
                          Competing Hypotheses ({caseDetail.hypotheses.length})
                        </h2>
                        <div className="space-y-3">
                          {caseDetail.hypotheses.map((h) => (
                            <div
                              key={h.hypothesis_id}
                              className="p-3.5 rounded-lg bg-slate-950/60 border border-slate-800 flex items-start justify-between gap-3 text-xs"
                            >
                              <div>
                                <span className="font-mono text-slate-400 block text-[10px]">{h.hypothesis_id}</span>
                                <p className="font-medium text-slate-200 mt-0.5">{h.claim}</p>
                              </div>
                              <span
                                className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${
                                  h.status === "SUPPORTED"
                                    ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/30"
                                    : "bg-slate-800 text-slate-400"
                                }`}
                              >
                                {h.status}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Proof Package */}
                      {caseDetail.proof && (
                        <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-5 space-y-3">
                          <div className="flex items-center justify-between">
                            <h2 className="text-sm font-bold text-slate-200 uppercase tracking-wider">
                              Falsifiable Proof Package
                            </h2>
                            <span className="px-2 py-0.5 rounded text-xs font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                              {caseDetail.proof.verifier_status}
                            </span>
                          </div>
                          <div className="space-y-2 text-xs text-slate-300">
                            <div><strong>Rule:</strong> {caseDetail.proof.verifier_rule_id} ({caseDetail.proof.verifier_rule_version})</div>
                            <div className="font-mono text-[11px] text-slate-400 break-all">
                              <strong>Digest:</strong> {caseDetail.proof.canonical_hash}
                            </div>
                          </div>
                        </div>
                      )}
                    </div>

                    {/* Right 1 Col: Dry-Run Preview & Action Bar */}
                    <div className="space-y-6">
                      {caseDetail.dry_run && (
                        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 space-y-4">
                          <h2 className="text-sm font-bold text-slate-200 uppercase tracking-wider">Dry-Run Preview</h2>
                          <div className="space-y-3 text-xs">
                            <div className="flex justify-between p-2 rounded bg-slate-950/60 border border-slate-800/80">
                              <span className="text-slate-400">Before Variance:</span>
                              <span className="font-bold text-amber-400">{formatINR(caseDetail.dry_run.variance_before_paise)}</span>
                            </div>
                            <div className="flex justify-between p-2 rounded bg-slate-950/60 border border-slate-800/80">
                              <span className="text-slate-400">Proposed Delta:</span>
                              <span className="font-bold text-emerald-400">{formatINR(caseDetail.dry_run.proposed_delta_paise)}</span>
                            </div>
                            <div className="flex justify-between p-2 rounded bg-slate-950/60 border border-slate-800/80">
                              <span className="text-slate-400">After Variance:</span>
                              <span className="font-bold text-emerald-400">{formatINR(caseDetail.dry_run.variance_after_paise)}</span>
                            </div>
                          </div>

                          {/* Action Buttons */}
                          {caseDetail.case.status === CaseStatus.APPROVAL_REQUIRED && (
                            <div className="pt-2 space-y-2">
                              <button
                                onClick={() => {
                                  setModalAction("APPROVE");
                                  setApprovalModalOpen(true);
                                }}
                                className="w-full rounded-lg bg-emerald-600 hover:bg-emerald-500 py-2.5 text-xs font-bold text-white shadow-md shadow-emerald-600/20 transition-all"
                              >
                                Approve Ledger Correction
                              </button>
                              <button
                                onClick={() => {
                                  setModalAction("REJECT");
                                  setApprovalModalOpen(true);
                                }}
                                className="w-full rounded-lg bg-slate-800 hover:bg-red-950/50 hover:border-red-500/50 border border-slate-700 py-2 text-xs font-semibold text-slate-300 transition-all"
                              >
                                Reject & Leave Unresolved
                              </button>
                            </div>
                          )}

                          {caseDetail.case.status === CaseStatus.SIMULATED_APPLIED && (
                            <div className="p-3 rounded-lg bg-blue-500/10 border border-blue-500/30 text-xs text-blue-300 space-y-1">
                              <div className="font-bold uppercase tracking-wider text-[10px]">Simulated Correction Applied</div>
                              <div className="font-mono text-[11px] truncate">Corr ID: {caseDetail.simulated_correction?.correction_id}</div>
                              <div className="font-mono text-[11px] truncate">Appr ID: {caseDetail.simulated_correction?.approval_id}</div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Evidence Graph Tab */}
                {activeTab === "graph" && (
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <h2 className="text-sm font-bold text-slate-200 uppercase tracking-wider">Flight Recorder Evidence Graph</h2>
                      <button
                        onClick={() => setGraphViewMode(graphViewMode === "visual" ? "table" : "visual")}
                        className="px-3 py-1 text-xs rounded bg-slate-800 border border-slate-700 text-slate-300 hover:text-white"
                      >
                        Switch to {graphViewMode === "visual" ? "Accessible Table View" : "Visual Graph View"}
                      </button>
                    </div>

                    {graphViewMode === "visual" ? (
                      <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-8 flex items-center justify-center min-h-[300px]">
                        <div className="flex flex-col md:flex-row items-center gap-6 text-xs">
                          <div className="p-4 rounded-xl bg-slate-900 border border-slate-700 text-center shadow-lg w-48">
                            <span className="text-[10px] text-slate-400 uppercase block font-semibold">Payment Event</span>
                            <span className="font-mono font-bold text-slate-200">PAYMENT</span>
                          </div>
                          <div className="text-emerald-400 font-mono text-sm">──[ matched ]──▶</div>
                          <div className="p-4 rounded-xl bg-slate-900 border border-slate-700 text-center shadow-lg w-48">
                            <span className="text-[10px] text-slate-400 uppercase block font-semibold">Settlement</span>
                            <span className="font-mono font-bold text-slate-200">SETTLEMENT</span>
                          </div>
                          <div className="text-amber-400 font-mono text-sm">──[ discrepancy ]──▶</div>
                          <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/40 text-center shadow-lg w-48">
                            <span className="text-[10px] text-amber-400 uppercase block font-semibold">Ledger Entry</span>
                            <span className="font-mono font-bold text-amber-300">LEDGER_ENTRY</span>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="rounded-xl border border-slate-800 overflow-hidden">
                        <table className="w-full text-left text-xs text-slate-300">
                          <thead className="bg-slate-900/80 border-b border-slate-800 text-[10px] uppercase font-bold text-slate-400">
                            <tr>
                              <th className="p-3">Record Type</th>
                              <th className="p-3">Record ID</th>
                              <th className="p-3">Note</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/60 bg-slate-950/40">
                            {caseDetail.case.evidence.map((e, idx) => (
                              <tr key={idx}>
                                <td className="p-3 font-semibold text-slate-200">{e.record_type}</td>
                                <td className="p-3 font-mono text-slate-400">{e.record_id}</td>
                                <td className="p-3 text-slate-400">{e.note || "Referenced in reconciliation"}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}

                {/* Audit Trail Tab */}
                {activeTab === "audit" && (
                  <div className="space-y-4">
                    <h2 className="text-sm font-bold text-slate-200 uppercase tracking-wider">Append-Only Audit Timeline</h2>
                    <div className="space-y-3">
                      {auditTrail.map((ev, idx) => (
                        <div key={idx} className="p-4 rounded-xl bg-slate-900/40 border border-slate-800 text-xs space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="font-semibold text-emerald-400">{ev.action}</span>
                            <span className="font-mono text-[10px] text-slate-500">{ev.timestamp_utc}</span>
                          </div>
                          <div className="text-slate-400">Actor: <span className="font-medium text-slate-300">{ev.actor}</span></div>
                          <div className="font-mono text-[10px] text-slate-500 truncate">Digest: {ev.digest}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center text-xs text-slate-500">
              Select an exception case to view workspace.
            </div>
          )}
        </main>
      </div>

      {/* Confirmation Modal */}
      {approvalModalOpen && caseDetail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 backdrop-blur-sm p-4">
          <div className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-6 shadow-2xl space-y-4">
            <h3 className="text-base font-bold text-white">
              {modalAction === "APPROVE" ? "Confirm Human Approval" : "Confirm Case Rejection"}
            </h3>
            <p className="text-xs text-slate-300">
              {modalAction === "APPROVE"
                ? `You are authorizing a simulated ledger adjustment of ${formatINR(
                    caseDetail.case.proposed_delta_paise ?? 0,
                  )} on case ${caseDetail.case.case_id}.`
                : `You are rejecting the proposed correction for case ${caseDetail.case.case_id}. It will remain marked as UNRESOLVED.`}
            </p>

            <textarea
              placeholder="Reviewer justification or notes..."
              value={reviewerNotes}
              onChange={(e) => setReviewerNotes(e.target.value)}
              className="w-full rounded-lg bg-slate-950 border border-slate-800 p-3 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-emerald-500"
              rows={3}
            />

            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setApprovalModalOpen(false)}
                disabled={actionLoading}
                className="px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-slate-300"
              >
                Cancel
              </button>
              <button
                onClick={() => void handleApproveOrReject()}
                disabled={actionLoading}
                className={`px-4 py-2 rounded-lg text-xs font-bold text-white transition-all ${
                  modalAction === "APPROVE"
                    ? "bg-emerald-600 hover:bg-emerald-500 shadow-md shadow-emerald-600/30"
                    : "bg-red-600 hover:bg-red-500 shadow-md shadow-red-600/30"
                }`}
              >
                {actionLoading
                  ? "Submitting..."
                  : modalAction === "APPROVE"
                    ? "Confirm & Apply"
                    : "Confirm Rejection"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
