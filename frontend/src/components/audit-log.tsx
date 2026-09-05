/**
 * Append-only audit timeline.
 *
 * Events are rendered in the backend's authoritative append order and each row
 * shows its storage sequence. Wall-clock stamps can tie or arrive out of
 * order, so the sequence — not the timestamp — is what makes the ordering
 * assertable. The scope of the trail is stated explicitly, because a case
 * trail and a run trail are different claims.
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

export interface AuditLogProps {
  events: AuditLogItem[];
  /** What this trail covers, e.g. "case-abc123" or "run-abc123". */
  scopeLabel: string;
  /** Copy shown when the scope legitimately recorded nothing. */
  emptyMessage: string;
  /** Set while the trail is being fetched, so empty is not shown too early. */
  loading?: boolean;
}

export function AuditLog({ events, scopeLabel, emptyMessage, loading = false }: AuditLogProps) {
  return (
    <Panel className="p-5">
      <SectionLabel
        right={
          <span className="text-[11px] font-medium text-slate-500">
            Append-only · SHA-256 digested · storage order
          </span>
        }
      >
        Audit flight log ({events.length})
      </SectionLabel>

      <p className="mt-1.5 font-mono text-[10px] text-slate-500">scope {scopeLabel}</p>

      <div aria-live="polite">
        {loading ? (
          <p className="mt-4 text-xs font-medium text-slate-500">Loading audit events…</p>
        ) : events.length === 0 ? (
          <p className="mt-4 text-xs font-medium text-slate-500">{emptyMessage}</p>
        ) : (
          <ol className="relative mt-4 space-y-0">
            <span aria-hidden className="absolute bottom-3 left-[7px] top-3 w-px bg-slate-200" />
            {events.map((event) => (
              <li key={event.event_id} className="relative pl-7">
                <span
                  aria-hidden
                  className={`absolute left-0 top-[14px] h-[15px] w-[15px] rounded-full border-2 border-white shadow-sm ${
                    event.actor.toUpperCase() === "USER"
                      ? "bg-emerald-500"
                      : event.actor.toUpperCase() === "MODEL"
                        ? "bg-purple-500"
                        : "bg-blue-500"
                  }`}
                />
                <div className="border-b border-slate-100 py-3 last:border-b-0">
                  <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                    <span className="flex items-baseline gap-2">
                      <span
                        className="font-mono text-[10px] font-bold tabular-nums text-slate-400"
                        title="Append-only storage sequence"
                      >
                        #{event.sequence}
                      </span>
                      <span className="select-all font-mono text-xs font-bold tracking-tight text-slate-900">
                        {event.action}
                      </span>
                    </span>
                    <span className="font-mono text-[11px] font-medium tabular-nums text-slate-500">
                      {formatUtc(event.timestamp_utc)}
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-2">
                    <Badge tone={actorTone(event.actor)}>{event.actor}</Badge>
                    <CopyChip value={event.event_id} label={event.event_id} />
                    {event.case_id ? (
                      <span className="font-mono text-[10px] text-slate-500">{event.case_id}</span>
                    ) : null}
                  </div>
                  {Object.keys(event.payload).length > 0 && (
                    <details className="group mt-2">
                      <summary className="cursor-pointer select-none text-[10px] font-bold uppercase tracking-wider text-slate-500 transition-colors hover:text-slate-800">
                        Payload
                      </summary>
                      <pre className="mt-1.5 max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-slate-200 bg-slate-50 p-2.5 font-mono text-[11px] leading-relaxed text-slate-800">
                        {JSON.stringify(event.payload, null, 2)}
                      </pre>
                    </details>
                  )}
                  <div
                    className="mt-1.5 select-all truncate font-mono text-[10px] font-medium text-slate-400"
                    title={event.digest}
                  >
                    digest {event.digest}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </Panel>
  );
}
