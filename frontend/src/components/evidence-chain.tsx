/**
 * Evidence chain: an honest flight-recorder trace built from the case's
 * actual evidence records (PRD §11 — the graph is a UI over explicit data
 * and must not imply certainty beyond stored edge state).
 */

"use client";

import { useState } from "react";
import type { EvidenceItem } from "../lib/types";
import { IconRoute } from "./icons";
import { Panel, SectionLabel, Badge } from "./primitives";

const TYPE_STYLES: Record<string, { hex: string; label: string }> = {
  PAYMENT: { hex: "#34d399", label: "Payment" },
  REFUND: { hex: "#a78bfa", label: "Refund" },
  SETTLEMENT: { hex: "#67e8f9", label: "Settlement" },
  BANK_ENTRY: { hex: "#e6b45c", label: "Bank entry" },
  LEDGER_ENTRY: { hex: "#f0abfc", label: "Ledger entry" },
};

function typeStyle(recordType: string): { hex: string; label: string } {
  return (
    TYPE_STYLES[recordType] ?? {
      hex: "#a1a1aa",
      label: recordType.replaceAll("_", " "),
    }
  );
}

export function EvidenceChain({ evidence }: { evidence: EvidenceItem[] }) {
  const [view, setView] = useState<"chain" | "table">("chain");

  const toggle = (
    <div className="flex rounded-lg border border-white/[0.08] bg-black/40 p-0.5">
      {(["chain", "table"] as const).map((mode) => (
        <button
          key={mode}
          onClick={() => setView(mode)}
          className={`rounded-md px-2.5 py-1 text-[10.5px] font-medium transition-colors focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c] ${
            view === mode ? "bg-white/[0.08] text-zinc-100" : "text-zinc-500 hover:text-zinc-300"
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
        <p className="mt-4 text-xs text-zinc-500">
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
            <span className="hidden text-[10px] text-zinc-600 md:inline">
              {evidence.length} record{evidence.length === 1 ? "" : "s"} · reconstructed from case evidence only
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
                    className="relative min-w-[150px] max-w-[210px] rounded-xl border bg-[#0c0c0f]/90 px-3.5 py-2.5"
                    style={{ borderColor: `${style.hex}33` }}
                  >
                    <span
                      aria-hidden
                      className="absolute -left-px -top-px h-2 w-2 rounded-tl-xl border-l border-t"
                      style={{ borderColor: `${style.hex}88` }}
                    />
                    <span
                      className="block text-[9px] font-semibold uppercase tracking-[0.14em]"
                      style={{ color: style.hex }}
                    >
                      {style.label}
                    </span>
                    <span className="mt-1 block select-all truncate font-mono text-[11px] font-medium text-zinc-200" title={item.record_id}>
                      {item.record_id}
                    </span>
                    {item.note && (
                      <span className="mt-1 block truncate text-[10px] leading-snug text-zinc-500" title={item.note}>
                        {item.note}
                      </span>
                    )}
                  </div>
                  {/* Connector */}
                  {idx < evidence.length - 1 && (
                    <span aria-hidden className="mx-1.5 flex items-center self-center text-zinc-700">
                      <svg width="26" height="10" viewBox="0 0 26 10" fill="none" stroke="currentColor" strokeWidth="1.2">
                        <path d="M0 5h21" strokeDasharray="3 3" />
                        <path d="m18 1.5 4 3.5-4 3.5" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
          <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1.5 border-t border-white/[0.05] pt-3">
            {Object.entries(TYPE_STYLES).map(([type, s]) => (
              <span key={type} className="inline-flex items-center gap-1.5 text-[9.5px] uppercase tracking-wider text-zinc-500">
                <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: s.hex }} />
                {s.label}
              </span>
            ))}
          </div>
        </>
      ) : (
        <div className="mt-4 overflow-hidden rounded-xl border border-white/[0.06]">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-white/[0.07] bg-white/[0.03] text-[9.5px] uppercase tracking-[0.14em] text-zinc-500">
              <tr>
                <th scope="col" className="px-3.5 py-2.5 font-semibold">Type</th>
                <th scope="col" className="px-3.5 py-2.5 font-semibold">Record</th>
                <th scope="col" className="px-3.5 py-2.5 font-semibold">Note</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.045]">
              {evidence.map((item, idx) => {
                const style = typeStyle(item.record_type);
                return (
                  <tr key={`${item.record_type}-${item.record_id}-${idx}`} className="transition-colors hover:bg-white/[0.02]">
                    <td className="whitespace-nowrap px-3.5 py-2.5">
                      <Badge tone="neutral">
                        <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: style.hex }} />
                        {style.label}
                      </Badge>
                    </td>
                    <td className="select-all whitespace-nowrap px-3.5 py-2.5 font-mono text-[11px] text-zinc-200">
                      {item.record_id}
                    </td>
                    <td className="px-3.5 py-2.5 text-[11px] text-zinc-500">{item.note ?? "\u2014"}</td>
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
