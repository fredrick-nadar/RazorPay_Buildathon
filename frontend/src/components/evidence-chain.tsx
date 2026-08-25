/**
 * Evidence chain: an honest flight-recorder trace built from the case's
 * actual evidence records (PRD §11). Clean, minimal, bright & professional.
 */

"use client";

import { useState } from "react";
import type { EvidenceItem } from "../lib/types";
import { IconRoute } from "./icons";
import { Panel, SectionLabel, Badge } from "./primitives";

const TYPE_STYLES: Record<string, { hex: string; bg: string; text: string; label: string }> = {
  PAYMENT: { hex: "#059669", bg: "#ecfdf5", text: "#065f46", label: "Payment" },
  REFUND: { hex: "#7c3aed", bg: "#f5f3ff", text: "#5b21b6", label: "Refund" },
  SETTLEMENT: { hex: "#0284c7", bg: "#f0f9ff", text: "#075985", label: "Settlement" },
  BANK_ENTRY: { hex: "#d97706", bg: "#fffbeb", text: "#92400e", label: "Bank entry" },
  LEDGER_ENTRY: { hex: "#c026d3", bg: "#fdf4ff", text: "#86198f", label: "Ledger entry" },
};

function typeStyle(recordType: string) {
  return (
    TYPE_STYLES[recordType] ?? {
      hex: "#64748b",
      bg: "#f8fafc",
      text: "#334155",
      label: recordType.replaceAll("_", " "),
    }
  );
}

export function EvidenceChain({ evidence }: { evidence: EvidenceItem[] }) {
  const [view, setView] = useState<"chain" | "table">("chain");

  const toggle = (
    <div className="flex rounded-lg border border-slate-200 bg-slate-100 p-0.5">
      {(["chain", "table"] as const).map((mode) => (
        <button
          key={mode}
          onClick={() => setView(mode)}
          className={`rounded-md px-2.5 py-1 text-[11px] font-semibold transition-colors ${
            view === mode ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-900"
          }`}
        >
          {mode === "chain" ? "Trace" : "Table"}
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
        <p className="mt-4 text-xs text-slate-500 font-medium">
          This case carries no linked evidence records.
        </p>
      </Panel>
    );
  }

  return (
    <Panel className="p-5">
      <SectionLabel
        accent
        right={
          <div className="flex items-center gap-3">
            <span className="hidden text-[11px] font-medium text-slate-500 md:inline">
              {evidence.length} record{evidence.length === 1 ? "" : "s"} · Reconstructed from case evidence only
            </span>
            {toggle}
          </div>
        }
      >
        <IconRoute size={13} /> Evidence trace
      </SectionLabel>

      {view === "chain" ? (
        <>
          <ol className="mt-5 flex flex-wrap items-stretch gap-y-4">
            {evidence.map((item, idx) => {
              const style = typeStyle(item.record_type);
              return (
                <li key={`${item.record_type}-${item.record_id}-${idx}`} className="flex items-stretch">
                  {/* Node */}
                  <div
                    className="relative min-w-[150px] max-w-[220px] rounded-xl border border-slate-200 bg-slate-50/70 px-3.5 py-2.5 shadow-sm"
                    style={{ borderTop: `3px solid ${style.hex}` }}
                  >
                    <span
                      className="block text-[10px] font-bold uppercase tracking-wider"
                      style={{ color: style.hex }}
                    >
                      {style.label}
                    </span>
                    <span className="mt-1 block select-all truncate font-mono text-xs font-bold text-slate-900" title={item.record_id}>
                      {item.record_id}
                    </span>
                    {item.note && (
                      <span className="mt-1 block truncate text-[10.5px] leading-snug text-slate-500" title={item.note}>
                        {item.note}
                      </span>
                    )}
                  </div>
                  {/* Connector */}
                  {idx < evidence.length - 1 && (
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
            {Object.entries(TYPE_STYLES).map(([type, s]) => (
              <span key={type} className="inline-flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-600">
                <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: s.hex }} />
                {s.label}
              </span>
            ))}
          </div>
        </>
      ) : (
        <div className="mt-4 overflow-hidden rounded-xl border border-slate-200">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-200 bg-slate-50 text-[10px] uppercase font-bold tracking-wider text-slate-600">
              <tr>
                <th scope="col" className="px-3.5 py-2.5">Type</th>
                <th scope="col" className="px-3.5 py-2.5">Record</th>
                <th scope="col" className="px-3.5 py-2.5">Note</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {evidence.map((item, idx) => {
                const style = typeStyle(item.record_type);
                return (
                  <tr key={`${item.record_type}-${item.record_id}-${idx}`} className="transition-colors hover:bg-slate-50/60">
                    <td className="whitespace-nowrap px-3.5 py-2.5">
                      <Badge tone="neutral">
                        <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: style.hex }} />
                        {style.label}
                      </Badge>
                    </td>
                    <td className="select-all whitespace-nowrap px-3.5 py-2.5 font-mono text-xs font-semibold text-slate-900">
                      {item.record_id}
                    </td>
                    <td className="px-3.5 py-2.5 text-xs text-slate-600">{item.note ?? "\u2014"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
