/**
 * Append-only audit timeline. Renders stored audit events with actor chips,
 * stable action codes, timestamps, and copyable SHA-256 digests.
 * Clean, minimal, bright & professional.
 */

"use client";

import type { AuditLogItem } from "../lib/types";
import { formatUtc } from "../lib/format";
import { Panel, SectionLabel, CopyChip, Badge, type BadgeTone } from "./primitives";

const ACTOR_TONE: Record<string, BadgeTone> = {
  SYSTEM: "info",
  USER: "positive",
  MODEL: "violet",
};

function actorTone(actor: string): BadgeTone {
  return ACTOR_TONE[actor.toUpperCase()] ?? "neutral";
}

export function AuditLog({ events }: { events: AuditLogItem[] }) {
  return (
    <Panel className="p-5">
      <SectionLabel
        right={
          <span className="text-[11px] font-medium text-slate-500">
            Append-only · SHA-256 digested
          </span>
        }
      >
        Audit flight log ({events.length})
      </SectionLabel>

      {events.length === 0 ? (
        <p className="mt-4 text-xs font-medium text-slate-500">
          No audit events recorded for this case yet.
        </p>
      ) : (
        <ol className="relative mt-4 space-y-0">
          {/* Rail */}
          <span aria-hidden className="absolute bottom-3 left-[7px] top-3 w-px bg-slate-200" />
          {events.map((ev) => (
            <li key={ev.event_id} className="relative pl-7">
              <span
                aria-hidden
                className={`absolute left-0 top-[14px] h-[15px] w-[15px] rounded-full border-2 border-white shadow-sm ${
                  ev.actor.toUpperCase() === "USER"
                    ? "bg-emerald-500"
                    : ev.actor.toUpperCase() === "MODEL"
                      ? "bg-purple-500"
                      : "bg-blue-500"
                }`}
              />
              <div className="border-b border-slate-100 py-3 last:border-b-0">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <span className="select-all font-mono text-xs font-bold tracking-tight text-slate-900">
                    {ev.action}
                  </span>
                  <span className="font-mono text-[11px] font-medium tabular-nums text-slate-500">
                    {formatUtc(ev.timestamp_utc)}
                  </span>
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  <Badge tone={actorTone(ev.actor)}>{ev.actor}</Badge>
                  <CopyChip value={ev.event_id} label={ev.event_id} />
                </div>
                {Object.keys(ev.payload).length > 0 && (
                  <details className="group mt-2">
                    <summary className="cursor-pointer select-none text-[10px] font-bold uppercase tracking-wider text-slate-500 transition-colors hover:text-slate-800">
                      Payload
                    </summary>
                    <pre className="mt-1.5 max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-slate-200 bg-slate-50 p-2.5 font-mono text-[11px] leading-relaxed text-slate-800">
                      {JSON.stringify(ev.payload, null, 2)}
                    </pre>
                  </details>
                )}
                <div className="mt-1.5 select-all truncate font-mono text-[10px] font-medium text-slate-400" title={ev.digest}>
                  digest {ev.digest}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
