/**
 * Evidence trace: the case's cited records, each resolved to the immutable
 * source row behind it (PRD 11).
 *
 * The trace previously showed a record type, an id and a note — enough to name
 * a record, not enough to trust the citation. The backend now resolves each
 * citation to its source revision, so this renders the content hash, the
 * source row and file, the accepted/quarantined state, and whether the
 * normalized revision still matches the source it points at.
 *
 * A citation the backend could not resolve is shown as unresolved with its
 * reason. It is never hidden and never given a placeholder value.
 */

"use client";

import { useState } from "react";
import type { EvidenceItem } from "../lib/types";
import { formatINR, shortHash } from "../lib/format";
import { IconRoute } from "./icons";
import { Panel, SectionLabel, Badge } from "./primitives";

const TYPE_STYLES: Record<string, { hex: string; label: string }> = {
  PAYMENT: { hex: "#059669", label: "Payment" },
  REFUND: { hex: "#7c3aed", label: "Refund" },
  SETTLEMENT: { hex: "#0284c7", label: "Settlement" },
  BANK_ENTRY: { hex: "#d97706", label: "Bank entry" },
  LEDGER_ENTRY: { hex: "#c026d3", label: "Ledger entry" },
};

function typeStyle(recordType: string) {
  return TYPE_STYLES[recordType] ?? { hex: "#64748b", label: recordType.replaceAll("_", " ") };
}

const RESOLUTION_COPY: Record<EvidenceItem["resolution"], string> = {
  RESOLVED: "Source row located; revision hash matched.",
  PARTIAL: "Normalized record found, but its source row could not be located.",
  UNRESOLVED: "This citation does not resolve to a record in this run.",
};

/** Which record types are actually cited here, for an honest legend. */
function citedTypes(evidence: EvidenceItem[]): string[] {
  const seen = new Set<string>();
  for (const item of evidence) seen.add(item.record_type);
  return [...seen];
}

export function EvidenceChain({ evidence }: { evidence: EvidenceItem[] }) {
  const [view, setView] = useState<"chain" | "table">("chain");

  const toggle = (
    <div role="group" aria-label="Evidence view" className="flex rounded-lg border border-slate-200 bg-slate-100 p-0.5">
      {(["chain", "table"] as const).map((mode) => (
        <button
          key={mode}
          type="button"
          onClick={() => setView(mode)}
          aria-pressed={view === mode}
          className={`rounded-md px-2.5 py-1 text-[11px] font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-slate-400 ${
            view === mode ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-900"
          }`}
        >
          {mode === "chain" ? "Trace" : "Provenance"}
        </button>
      ))}
    </div>
  );

  if (evidence.length === 0) {
    return (
      <Panel className="p-5">
        <SectionLabel accent right={toggle}>
          <IconRoute size={13} /> Evidence trace
        </SectionLabel>
        <p className="mt-4 text-xs font-medium text-slate-500">
          This case cites no evidence records. That is itself a finding: a case without cited
          evidence cannot be verified and cannot be resolved.
        </p>
      </Panel>
    );
  }

  const unresolvedCount = evidence.filter((item) => item.resolution !== "RESOLVED").length;
  const legend = citedTypes(evidence);

  return (
    <Panel className="p-5">
      <SectionLabel
        accent
        right={
          <div className="flex items-center gap-3">
            <span className="hidden text-[11px] font-medium text-slate-500 md:inline">
              {evidence.length} cited record{evidence.length === 1 ? "" : "s"}
            </span>
            {toggle}
          </div>
        }
      >
        <IconRoute size={13} /> Evidence trace
      </SectionLabel>

      {unresolvedCount > 0 && (
        <p
          role="status"
          className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] font-medium text-amber-900"
        >
          {unresolvedCount} of {evidence.length} citations did not fully resolve to a source row in
          this run. They are listed below with their reason rather than omitted.
        </p>
      )}

      {view === "chain" ? (
        <>
          <ol className="mt-5 flex flex-wrap items-stretch gap-y-4">
            {evidence.map((item, index) => {
              const style = typeStyle(item.record_type);
              const unresolved = item.resolution === "UNRESOLVED";
              return (
                <li
                  key={`${item.record_type}-${item.record_id}-${index}`}
                  className="flex items-stretch"
                >
                  <div
                    className={`relative min-w-[168px] max-w-[240px] rounded-xl border px-3.5 py-2.5 shadow-sm ${
                      unresolved
                        ? "border-amber-300 bg-amber-50/60"
                        : "border-slate-200 bg-slate-50/70"
                    }`}
                    style={{ borderTop: `3px solid ${unresolved ? "#d97706" : style.hex}` }}
                  >
                    <span
                      className="block text-[10px] font-bold uppercase tracking-wider"
                      style={{ color: unresolved ? "#92400e" : style.hex }}
                    >
                      {style.label}
                    </span>
                    <span
                      className="mt-1 block select-all truncate font-mono text-xs font-bold text-slate-900"
                      title={item.record_id}
                    >
                      {item.record_id}
                    </span>
                    {item.amount_paise !== null ? (
                      <span className="mt-0.5 block font-mono text-[10.5px] tabular-nums text-slate-600">
                        {formatINR(item.amount_paise)}
                      </span>
                    ) : null}
                    {item.content_hash ? (
                      <span
                        className="mt-0.5 block font-mono text-[10px] text-slate-400"
                        title={item.content_hash}
                      >
                        {shortHash(item.content_hash, 10)}
                      </span>
                    ) : null}
                    {unresolved ? (
                      <span className="mt-1 block text-[10px] font-semibold text-amber-800">
                        {item.resolution_reason ?? "unresolved"}
                      </span>
                    ) : null}
                    {item.note ? (
                      <span
                        className="mt-1 block truncate text-[10.5px] leading-snug text-slate-500"
                        title={item.note}
                      >
                        {item.note}
                      </span>
                    ) : null}
                  </div>
                  {index < evidence.length - 1 && (
                    <span aria-hidden className="mx-2 flex items-center self-center text-slate-400">
                      <svg width="24" height="10" viewBox="0 0 24 10" fill="none" stroke="currentColor" strokeWidth="1.5">
                        <path d="M0 5h19" strokeDasharray="3 3" />
                        <path d="m16 1.5 4 3.5-4 3.5" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
          <div className="mt-5 flex flex-wrap gap-x-4 gap-y-1.5 border-t border-slate-100 pt-3">
            {legend.map((type) => {
              const style = typeStyle(type);
              return (
                <span
                  key={type}
                  className="inline-flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-600"
                >
                  <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: style.hex }} />
                  {style.label}
                </span>
              );
            })}
          </div>
        </>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
          <table className="w-full min-w-[820px] text-left text-xs">
            <caption className="sr-only">
              Cited evidence records with their immutable source provenance
            </caption>
            <thead className="border-b border-slate-200 bg-slate-50 text-[10px] font-bold uppercase tracking-wider text-slate-600">
              <tr>
                <th scope="col" className="px-3.5 py-2.5">Type</th>
                <th scope="col" className="px-3.5 py-2.5">Record</th>
                <th scope="col" className="px-3.5 py-2.5">Amount</th>
                <th scope="col" className="px-3.5 py-2.5">Source revision</th>
                <th scope="col" className="px-3.5 py-2.5">Origin</th>
                <th scope="col" className="px-3.5 py-2.5">Resolution</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {evidence.map((item, index) => {
                const style = typeStyle(item.record_type);
                return (
                  <tr
                    key={`${item.record_type}-${item.record_id}-${index}`}
                    className="align-top transition-colors hover:bg-slate-50/60"
                  >
                    <td className="whitespace-nowrap px-3.5 py-2.5">
                      <Badge tone="neutral">
                        <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: style.hex }} />
                        {style.label}
                      </Badge>
                    </td>
                    <td className="select-all whitespace-nowrap px-3.5 py-2.5 font-mono text-xs font-semibold text-slate-900">
                      {item.record_id}
                    </td>
                    <td className="whitespace-nowrap px-3.5 py-2.5 font-mono text-xs tabular-nums text-slate-800">
                      {item.amount_paise !== null ? formatINR(item.amount_paise) : "—"}
                    </td>
                    <td className="px-3.5 py-2.5 font-mono text-[10.5px] text-slate-600">
                      {item.source_revision_id ? (
                        <span className="block select-all" title={item.source_revision_id}>
                          {item.source_revision_id}
                        </span>
                      ) : null}
                      {item.content_hash ? (
                        <span className="block select-all" title={item.content_hash}>
                          {shortHash(item.content_hash, 18)}
                        </span>
                      ) : (
                        <span className="block">—</span>
                      )}
                      {item.source_row_number !== null ? (
                        <span className="block text-slate-400">row {item.source_row_number}</span>
                      ) : null}
                      {item.revision_matches_source === false ? (
                        <span className="block font-sans font-bold text-amber-700">
                          differs from source row
                        </span>
                      ) : null}
                    </td>
                    <td className="px-3.5 py-2.5 text-[10.5px] text-slate-600">
                      {item.source_origin ? (
                        <span className="block font-semibold text-slate-800">
                          {item.source_origin}
                        </span>
                      ) : null}
                      {item.source_file ? (
                        <span className="block break-all font-mono">{item.source_file}</span>
                      ) : (
                        <span className="block">—</span>
                      )}
                      {item.source_state ? (
                        <span className="block text-slate-400">{item.source_state}</span>
                      ) : null}
                    </td>
                    <td className="px-3.5 py-2.5 text-[10.5px]">
                      <span
                        className={`font-bold ${
                          item.resolution === "RESOLVED" ? "text-emerald-700" : "text-amber-700"
                        }`}
                      >
                        {item.resolution}
                      </span>
                      <span className="mt-0.5 block text-slate-500">
                        {item.resolution_reason ?? RESOLUTION_COPY[item.resolution]}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-4 border-t border-slate-100 pt-3 text-[10px] leading-relaxed text-slate-500">
        Reconstructed from stored case evidence and the immutable source rows it points at. Hashes
        are content digests over the imported row, not an external attestation.
      </p>
    </Panel>
  );
}
