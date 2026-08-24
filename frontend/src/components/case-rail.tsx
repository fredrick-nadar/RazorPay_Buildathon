/**
 * Exception case rail: search, filters, and the case queue.
 * Renders API results only.
 */

"use client";

import { CaseStatus } from "../domain/enums";
import type { CaseSummary } from "../lib/types";
import { formatINR, humanizeEnum } from "../lib/format";
import { IconCornerUpLeft, IconClock, IconLayers, IconQuestion, IconSearch, IconCheck, IconDoubleCheck, IconFlag, IconX, IconRefresh } from "./icons";
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
    hex: "#e6b45c",
  },
  MISSING_REFUND_POSTING: {
    label: "Missing refund",
    icon: <IconCornerUpLeft size={13} />,
    tone: "violet",
    hex: "#a78bfa",
  },
  SETTLEMENT_TIMING_WINDOW_SHIFT: {
    label: "Timing shift",
    icon: <IconClock size={13} />,
    tone: "info",
    hex: "#67e8f9",
  },
  AMBIGUOUS_EVIDENCE: {
    label: "Ambiguous evidence",
    icon: <IconQuestion size={13} />,
    tone: "critical",
    hex: "#fda4af",
  },
};

export function categoryMeta(category: string): CategoryMeta {
  return (
    CATEGORY_META[category] ?? {
      label: humanizeEnum(category),
      icon: <IconQuestion size={13} />,
      tone: "critical" as BadgeTone,
      hex: "#fda4af",
    }
  );
}

const STATUS_META: Record<string, { label: string; tone: BadgeTone; icon: ReactNode }> = {
  [CaseStatus.OPEN]: { label: "Open", tone: "neutral", icon: <IconRefresh size={11} /> },
  [CaseStatus.INVESTIGATING]: { label: "Investigating", tone: "info", icon: <IconSearch size={11} /> },
  [CaseStatus.VERIFICATION_FAILED]: { label: "Verification failed", tone: "critical", icon: <IconX size={11} /> },
  [CaseStatus.VERIFIED_RESOLVED]: { label: "Verified resolved", tone: "positive", icon: <IconCheck size={11} /> },
  [CaseStatus.APPROVAL_REQUIRED]: { label: "Approval required", tone: "brass", icon: <IconShieldSmall /> },
  [CaseStatus.SIMULATED_APPLIED]: { label: "Simulated applied", tone: "violet", icon: <IconDoubleCheck size={11} /> },
  [CaseStatus.UNRESOLVED]: { label: "Unresolved", tone: "critical", icon: <IconFlag size={11} /> },
  [CaseStatus.INVESTIGATION_FAILED]: { label: "Investigation failed", tone: "critical", icon: <IconX size={11} /> },
};

function IconShieldSmall() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
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
}: {
  cases: CaseSummary[];
  loading: boolean;
  selectedCaseId: string | null;
  onSelect: (caseId: string) => void;
  statusFilter: string;
  onStatusFilter: (value: string) => void;
  categoryFilter: string;
  onCategoryFilter: (value: string) => void;
  searchQuery: string;
  onSearchQuery: (value: string) => void;
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
    <aside className="flex w-[320px] shrink-0 flex-col border-r border-white/[0.06] bg-[#0b0b0e]/80 xl:w-[360px]">
      {/* Filters */}
      <div className="space-y-3 border-b border-white/[0.06] px-4 pb-3.5 pt-4">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-[0.16em] text-zinc-400">
            Exception queue
            <span className="rounded-full border border-white/10 bg-white/[0.04] px-1.5 py-px font-mono text-[10px] tracking-normal text-zinc-400">
              {filtered.length}/{cases.length}
            </span>
          </h2>
          <select
            aria-label="Filter by category"
            value={categoryFilter}
            onChange={(e) => onCategoryFilter(e.target.value)}
            className="max-w-[130px] truncate rounded-lg border border-white/[0.08] bg-black/50 px-2 py-1 text-[11px] text-zinc-300 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c]"
          >
            {CATEGORY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <div className="relative">
          <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-600">
            <IconSearch size={13} />
          </span>
          <input
            type="text"
            placeholder="Search case ID, category, summary…"
            value={searchQuery}
            onChange={(e) => onSearchQuery(e.target.value)}
            aria-label="Search cases"
            className="w-full rounded-lg border border-white/[0.08] bg-black/50 py-1.5 pl-8 pr-3 text-xs text-zinc-200 placeholder-zinc-600 transition-colors focus-visible:border-[#e6b45c]/40 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c]"
          />
        </div>

        <div className="flex gap-1 overflow-x-auto pb-0.5">
          {STATUS_FILTERS.map((st) => {
            const active = statusFilter === st.value;
            return (
              <button
                key={st.value}
                onClick={() => onStatusFilter(st.value)}
                className={`whitespace-nowrap rounded-full px-2.5 py-1 text-[10.5px] font-medium transition-colors focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c] ${
                  active
                    ? "bg-[#e6b45c]/[0.14] text-[#e6b45c] ring-1 ring-inset ring-[#e6b45c]/30"
                    : "text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-300"
                }`}
              >
                {st.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Queue */}
      <div className="flex-1 space-y-1.5 overflow-y-auto p-2.5">
        {loading && (
          <>
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="mx-1 h-[92px]" />
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
                className={`group block w-full rounded-xl border p-3.5 text-left transition-all duration-150 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c] ${
                  selected
                    ? "border-[#e6b45c]/25 bg-gradient-to-br from-[#16130c] to-[#101013] shadow-[0_0_0_1px_rgba(230,180,92,0.12),0_12px_32px_-16px_rgba(0,0,0,0.9)]"
                    : "border-white/[0.055] bg-white/[0.02] hover:border-white/[0.11] hover:bg-white/[0.035]"
                }`}
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span
                    className={`font-mono text-[11.5px] font-semibold tracking-tight ${
                      selected ? "text-[#ecd9ae]" : "text-zinc-200"
                    }`}
                  >
                    {c.case_id}
                  </span>
                  <StatusBadge status={c.status} />
                </div>

                <div
                  className="mb-2.5 flex items-center gap-1.5 text-[11px] font-medium"
                  style={{ color: cat.hex }}
                >
                  {cat.icon}
                  <span className="truncate">{cat.label}</span>
                </div>

                <div className="flex items-center justify-between border-t border-white/[0.05] pt-2">
                  <span className="text-[10px] uppercase tracking-wider text-zinc-600">
                    Variance{" "}
                    <span className="ml-1 font-mono text-[11px] font-semibold normal-case tracking-normal text-[#e6b45c]">
                      {formatINR(c.variance_paise)}
                    </span>
                  </span>
                  {c.proposed_delta_paise !== null && (
                    <span className="font-mono text-[10.5px] font-semibold text-emerald-300/90">
                      Δ {formatINR(c.proposed_delta_paise)}
                    </span>
                  )}
                </div>
              </button>
            );
          })}

        {!loading && filtered.length === 0 && (
          <div className="mt-16 px-6 text-center">
            <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-xl border border-white/[0.07] bg-white/[0.03] text-zinc-500">
              <IconSearch size={16} />
            </div>
            <p className="text-xs font-medium text-zinc-400">No exceptions match</p>
            <p className="mt-1 text-[11px] leading-relaxed text-zinc-600">
              Adjust the filters or run a new batch to populate the queue.
            </p>
          </div>
        )}
      </div>
    </aside>
  );
}
