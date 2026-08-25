/**
 * Exception case rail: search, filters, and the case queue.
 * Clean, minimal, bright & professional.
 */

"use client";

import { CaseStatus } from "../domain/enums";
import type { CaseSummary } from "../lib/types";
import { formatINR, humanizeEnum } from "../lib/format";
import {
  IconCornerUpLeft,
  IconClock,
  IconLayers,
  IconQuestion,
  IconSearch,
  IconCheck,
  IconDoubleCheck,
  IconFlag,
  IconX,
  IconRefresh,
} from "./icons";
import { Badge, Skeleton, type BadgeTone } from "./primitives";
import type { ReactNode } from "react";

/* ------------------------------------------------------------------ */
/* Category + status metadata                                          */
/* ------------------------------------------------------------------ */

export interface CategoryMeta {
  label: string;
  icon: ReactNode;
  tone: BadgeTone;
  hex: string;
}

export const CATEGORY_META: Record<string, CategoryMeta> = {
  DUPLICATE_LEDGER_POSTING: {
    label: "Duplicate posting",
    icon: <IconLayers size={13} />,
    tone: "warning",
    hex: "#d97706",
  },
  MISSING_REFUND_POSTING: {
    label: "Missing refund",
    icon: <IconCornerUpLeft size={13} />,
    tone: "violet",
    hex: "#7c3aed",
  },
  SETTLEMENT_TIMING_WINDOW_SHIFT: {
    label: "Timing shift",
    icon: <IconClock size={13} />,
    tone: "info",
    hex: "#0284c7",
  },
  AMBIGUOUS_EVIDENCE: {
    label: "Ambiguous evidence",
    icon: <IconQuestion size={13} />,
    tone: "critical",
    hex: "#e11d48",
  },
};

export function categoryMeta(category: string): CategoryMeta {
  return (
    CATEGORY_META[category] ?? {
      label: humanizeEnum(category),
      icon: <IconQuestion size={13} />,
      tone: "critical" as BadgeTone,
      hex: "#e11d48",
    }
  );
}

const STATUS_META: Record<string, { label: string; tone: BadgeTone; icon: ReactNode }> = {
  [CaseStatus.OPEN]: { label: "Open", tone: "neutral", icon: <IconRefresh size={11} /> },
  [CaseStatus.INVESTIGATING]: { label: "Investigating", tone: "info", icon: <IconSearch size={11} /> },
  [CaseStatus.VERIFICATION_FAILED]: { label: "Verification failed", tone: "critical", icon: <IconX size={11} /> },
  [CaseStatus.VERIFIED_RESOLVED]: { label: "Verified resolved", tone: "positive", icon: <IconCheck size={11} /> },
  [CaseStatus.APPROVAL_REQUIRED]: { label: "Approval required", tone: "warning", icon: <IconShieldSmall /> },
  [CaseStatus.SIMULATED_APPLIED]: { label: "Simulated applied", tone: "violet", icon: <IconDoubleCheck size={11} /> },
  [CaseStatus.UNRESOLVED]: { label: "Unresolved", tone: "critical", icon: <IconFlag size={11} /> },
  [CaseStatus.INVESTIGATION_FAILED]: { label: "Investigation failed", tone: "critical", icon: <IconX size={11} /> },
};

function IconShieldSmall() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 2.5 4.5 5.5v6c0 4.7 3.2 8.1 7.5 10 4.3-1.9 7.5-5.3 7.5-10v-6L12 2.5Z" />
    </svg>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_META[status];
  if (!meta) return <Badge tone="neutral">{humanizeEnum(status)}</Badge>;
  return (
    <Badge tone={meta.tone} icon={meta.icon}>
      {meta.label}
    </Badge>
  );
}

/* ------------------------------------------------------------------ */
/* Rail                                                                */
/* ------------------------------------------------------------------ */

const CATEGORY_OPTIONS = [
  { value: "ALL", label: "All categories" },
  { value: "DUPLICATE_LEDGER_POSTING", label: "Duplicate posting" },
  { value: "MISSING_REFUND_POSTING", label: "Missing refund" },
  { value: "SETTLEMENT_TIMING_WINDOW_SHIFT", label: "Timing shift" },
  { value: "AMBIGUOUS_EVIDENCE", label: "Ambiguous" },
];

const STATUS_FILTERS = [
  { value: "ALL", label: "All" },
  { value: CaseStatus.APPROVAL_REQUIRED, label: "Approval" },
  { value: CaseStatus.VERIFIED_RESOLVED, label: "Resolved" },
  { value: CaseStatus.SIMULATED_APPLIED, label: "Applied" },
  { value: CaseStatus.UNRESOLVED, label: "Unresolved" },
];

export function CaseRail({
  cases,
  loading,
  selectedCaseId,
  onSelect,
  statusFilter,
  onStatusFilter,
  categoryFilter,
  onCategoryFilter,
  searchQuery,
  onSearchQuery,
  title,
  hideStatusFilters = false,
}: {
  cases: CaseSummary[];
  loading: boolean;
  selectedCaseId: string | null;
  onSelect: (caseId: string) => void;
  statusFilter: string;
  onStatusFilter?: (value: string) => void;
  categoryFilter: string;
  onCategoryFilter: (value: string) => void;
  searchQuery: string;
  onSearchQuery: (value: string) => void;
  title?: string;
  hideStatusFilters?: boolean;
}) {
  const filtered = cases.filter((c) => {
    if (statusFilter !== "ALL" && c.status !== statusFilter) return false;
    if (categoryFilter !== "ALL" && c.category !== categoryFilter) return false;
    const q = searchQuery.trim().toLowerCase();
    if (q) {
      const hit =
        c.case_id.toLowerCase().includes(q) ||
        c.category.toLowerCase().includes(q) ||
        c.summary.toLowerCase().includes(q);
      if (!hit) return false;
    }
    return true;
  });

  return (
    <aside className="flex w-[320px] shrink-0 flex-col border-r border-slate-200 bg-white xl:w-[350px]">
      {/* Filters Header */}
      <div className="space-y-2.5 border-b border-slate-100 p-3.5">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-slate-800">
            {title ?? "Exception queue"}
            <span className="rounded-full border border-slate-200 bg-slate-100 px-2 py-0.5 font-mono text-[10px] font-semibold text-slate-700">
              {filtered.length}
            </span>
          </h2>
          <select
            aria-label="Filter by category"
            value={categoryFilter}
            onChange={(e) => onCategoryFilter(e.target.value)}
            suppressHydrationWarning
            className="max-w-[130px] truncate rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-medium text-slate-700 focus:border-slate-400 focus:bg-white focus:outline-none"
          >
            {CATEGORY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <div className="relative">
          <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400">
            <IconSearch size={13} />
          </span>
          <input
            type="text"
            placeholder="Search case ID, category..."
            value={searchQuery}
            onChange={(e) => onSearchQuery(e.target.value)}
            aria-label="Search cases"
            suppressHydrationWarning
            className="w-full rounded-lg border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-3 text-xs text-slate-900 placeholder:text-slate-400 focus:border-slate-400 focus:bg-white focus:outline-none transition-all"
          />
        </div>

        {!hideStatusFilters && onStatusFilter && (
          <div className="flex gap-1 overflow-x-auto pb-0.5">
            {STATUS_FILTERS.map((st) => {
              const active = statusFilter === st.value;
              return (
                <button
                  key={st.value}
                  onClick={() => onStatusFilter(st.value)}
                  suppressHydrationWarning
                  className={`whitespace-nowrap rounded-md px-2.5 py-1 text-[11px] font-semibold transition-all ${
                    active
                      ? "bg-slate-900 text-white shadow-sm"
                      : "bg-slate-100 text-slate-600 hover:bg-slate-200 hover:text-slate-900"
                  }`}
                >
                  {st.label}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Case List Queue */}
      <div className="flex-1 space-y-2 overflow-y-auto p-3">
        {loading && (
          <>
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-[96px]" />
            ))}
          </>
        )}

        {!loading &&
          filtered.map((c) => {
            const selected = c.case_id === selectedCaseId;
            const cat = categoryMeta(c.category);
            return (
              <button
                key={c.case_id}
                onClick={() => onSelect(c.case_id)}
                aria-current={selected ? "true" : undefined}
                className={`group block w-full rounded-xl border p-3.5 text-left transition-all duration-150 ${
                  selected
                    ? "border-blue-600 bg-blue-50/50 shadow-sm ring-1 ring-blue-600/30"
                    : "border-slate-200/90 bg-white hover:border-slate-300 hover:bg-slate-50/60"
                }`}
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span
                    className={`font-mono text-xs font-bold tracking-tight ${
                      selected ? "text-blue-900" : "text-slate-900"
                    }`}
                  >
                    {c.case_id}
                  </span>
                  <StatusBadge status={c.status} />
                </div>

                <div
                  className="mb-2 flex items-center gap-1.5 text-[11.5px] font-semibold"
                  style={{ color: cat.hex }}
                >
                  {cat.icon}
                  <span className="truncate">{cat.label}</span>
                </div>

                <div className="flex items-center justify-between border-t border-slate-100 pt-2 text-xs">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
                    Variance{" "}
                    <span className="ml-1 font-mono text-[11.5px] font-bold normal-case tracking-normal text-slate-900">
                      {formatINR(c.variance_paise)}
                    </span>
                  </span>
                  {c.proposed_delta_paise !== null && (
                    <span className="font-mono text-[11px] font-bold text-emerald-700">
                      Δ {formatINR(c.proposed_delta_paise)}
                    </span>
                  )}
                </div>
              </button>
            );
          })}

        {!loading && filtered.length === 0 && (
          <div className="mt-16 px-6 text-center">
            <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-slate-400">
              <IconSearch size={16} />
            </div>
            <p className="text-xs font-semibold text-slate-700">No exceptions match</p>
            <p className="mt-1 text-[11px] text-slate-500 leading-relaxed">
              Adjust the filters or run a new reconciliation batch to populate the queue.
            </p>
          </div>
        )}
      </div>
    </aside>
  );
}
