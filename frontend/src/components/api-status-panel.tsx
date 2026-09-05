"use client";

/**
 * API status.
 *
 * This replaces a non-interactive sidebar div titled "API Status: Operational"
 * with a permanently animated green dot that called no endpoint at all — it
 * reported "operational" on an instance with no credentials configured.
 *
 * The four states stay distinct, exactly as the backend reports them:
 * NOT_CONFIGURED, CONFIGURED, REACHABLE and FAILED. Being configured is never
 * shown as being reachable. Reachability requires an explicit probe, and the
 * time of that probe is always shown, so a stale success cannot read as
 * current. Loading the page performs no probe and therefore no provider call.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { formatUtc } from "../lib/format";
import type { IntegrationState, IntegrationStatusItem, IntegrationStatusResponse } from "../lib/types";
import { IconActivity, IconRefresh } from "./icons";

type LoadState = "LOADING" | "READY" | "UNAVAILABLE";

const STATE_COPY: Record<IntegrationState, { label: string; detail: string; tone: string; dot: string }> = {
  REACHABLE: {
    label: "Reachable",
    detail: "A probe from this server succeeded at the time shown.",
    tone: "border-emerald-200 bg-emerald-50 text-emerald-900",
    dot: "bg-emerald-500",
  },
  FAILED: {
    label: "Probe failed",
    detail: "Configured, but the last probe did not succeed.",
    tone: "border-rose-200 bg-rose-50 text-rose-900",
    dot: "bg-rose-500",
  },
  CONFIGURED: {
    label: "Configured, not probed",
    detail: "Settings are present. Nothing has been contacted, so reachability is unknown.",
    tone: "border-slate-300 bg-slate-50 text-slate-800",
    dot: "bg-slate-400",
  },
  NOT_CONFIGURED: {
    label: "Not configured",
    detail: "Nothing is set up, so nothing can be reached.",
    tone: "border-slate-200 bg-white text-slate-600",
    dot: "bg-slate-300",
  },
};

function isStatusResponse(value: unknown): value is IntegrationStatusResponse {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<IntegrationStatusResponse>;
  return (
    typeof candidate.observed_at_utc === "string" &&
    Array.isArray(candidate.probed) &&
    Array.isArray(candidate.integrations) &&
    candidate.integrations.every(
      (item) =>
        typeof item?.name === "string" &&
        typeof item.label === "string" &&
        typeof item.configured === "boolean" &&
        typeof item.probe_performed === "boolean" &&
        item.state in STATE_COPY,
    )
  );
}

export function ApiStatusPanel() {
  const [data, setData] = useState<IntegrationStatusResponse | null>(null);
  const [state, setState] = useState<LoadState>("LOADING");
  const [probing, setProbing] = useState<string | null>(null);
  const requestId = useRef(0);

  const load = useCallback(async (probe?: string) => {
    const generation = ++requestId.current;
    if (probe) setProbing(probe);
    else setState((current) => (current === "READY" ? current : "LOADING"));
    try {
      const query = probe ? `?probe=${encodeURIComponent(probe)}` : "";
      const response = await fetch(`/api/v1/status/integrations${query}`);
      if (generation !== requestId.current) return;
      if (!response.ok) {
        setData(null);
        setState("UNAVAILABLE");
        return;
      }
      const body: unknown = await response.json();
      if (generation !== requestId.current) return;
      if (!isStatusResponse(body)) {
        setData(null);
        setState("UNAVAILABLE");
        return;
      }
      setData(body);
      setState("READY");
    } catch {
      if (generation === requestId.current) {
        setData(null);
        setState("UNAVAILABLE");
      }
    } finally {
      if (generation === requestId.current) setProbing(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-slate-50/40 p-4 sm:p-6">
      <div className="mx-auto w-full max-w-4xl space-y-4">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-slate-900 text-white">
              <IconActivity size={17} />
            </span>
            <div>
              <h2 className="text-base font-bold tracking-tight text-slate-900">
                API &amp; integration status
              </h2>
              <p className="mt-0.5 text-xs text-slate-500">
                Configured does not mean reachable. Probe an integration to establish reachability.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-[11px] font-semibold text-slate-700 transition hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-300"
          >
            <IconRefresh size={13} />
            Re-read configuration
          </button>
        </header>

        <div aria-live="polite">
          {state === "LOADING" && (
            <p className="rounded-2xl border border-slate-200 bg-white p-5 text-xs text-slate-500">
              Reading integration configuration…
            </p>
          )}

          {state === "UNAVAILABLE" && (
            <div role="alert" className="rounded-2xl border border-slate-300 bg-white p-5">
              <p className="text-xs font-bold text-slate-950">Status is unavailable</p>
              <p className="mt-1 text-[11px] text-slate-600">
                The backend did not answer, so no integration is described. Nothing here is
                presented as reachable.
              </p>
              <button
                type="button"
                onClick={() => void load()}
                className="mt-3 rounded-lg border border-slate-900 bg-white px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-900 hover:bg-slate-50"
              >
                Retry status
              </button>
            </div>
          )}
        </div>

        {state === "READY" && data && (
          <>
            <ul className="space-y-3">
              {data.integrations.map((item) => (
                <IntegrationRow
                  key={item.name}
                  item={item}
                  probing={probing === item.name}
                  onProbe={() => void load(item.name)}
                />
              ))}
            </ul>

            <footer className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
              <p className="text-[11px] leading-relaxed text-slate-600">{data.notice}</p>
              <p className="mt-1.5 font-mono text-[10px] text-slate-500">
                configuration read {formatUtc(data.observed_at_utc)}
                {data.probed.length > 0
                  ? ` · probed ${data.probed.join(", ")}`
                  : " · no probe requested in this read"}
              </p>
            </footer>
          </>
        )}
      </div>
    </div>
  );
}

function IntegrationRow({
  item,
  probing,
  onProbe,
}: {
  item: IntegrationStatusItem;
  probing: boolean;
  onProbe: () => void;
}) {
  const copy = STATE_COPY[item.state];
  const note = typeof item.detail.note === "string" ? item.detail.note : null;
  const chain = Array.isArray(item.detail.provider_chain)
    ? (item.detail.provider_chain as unknown[]).filter(
        (entry): entry is string => typeof entry === "string",
      )
    : [];
  const maskedKey =
    typeof item.detail.key_id_masked === "string" ? item.detail.key_id_masked : null;
  const schemaVersion =
    typeof item.detail.schema_version === "number" ? item.detail.schema_version : null;

  return (
    <li className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${copy.dot}`} />
            <h3 className="text-sm font-bold text-slate-900">{item.label}</h3>
            <span
              data-testid={`integration-state-${item.name}`}
              className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${copy.tone}`}
            >
              {copy.label}
            </span>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-600">{copy.detail}</p>
          {note ? <p className="mt-1 text-[11px] text-slate-500">{note}</p> : null}
        </div>

        {item.probeable ? (
          <button
            type="button"
            onClick={onProbe}
            disabled={probing || !item.configured}
            className="shrink-0 rounded-lg border border-slate-900 bg-white px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-900 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400"
          >
            {probing ? "Probing…" : "Probe now"}
          </button>
        ) : null}
      </div>

      <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 border-t border-slate-100 pt-3 font-mono text-[10px] text-slate-500">
        <div className="flex gap-1.5">
          <dt>configured</dt>
          <dd className="font-bold text-slate-800">{item.configured ? "yes" : "no"}</dd>
        </div>
        <div className="flex gap-1.5">
          <dt>last checked</dt>
          <dd className="font-bold text-slate-800">
            {item.last_checked_utc ? formatUtc(item.last_checked_utc) : "never"}
          </dd>
        </div>
        {item.probe_reason ? (
          <div className="flex gap-1.5">
            <dt>reason</dt>
            <dd className="font-bold text-rose-700">{item.probe_reason}</dd>
          </div>
        ) : null}
        {maskedKey ? (
          <div className="flex gap-1.5">
            <dt>key</dt>
            <dd className="font-bold text-slate-800">{maskedKey}</dd>
          </div>
        ) : null}
        {schemaVersion !== null ? (
          <div className="flex gap-1.5">
            <dt>schema</dt>
            <dd className="font-bold text-slate-800">v{schemaVersion}</dd>
          </div>
        ) : null}
        {chain.length > 0 ? (
          <div className="flex gap-1.5">
            <dt>chain</dt>
            <dd className="font-bold text-slate-800">{chain.join(" → ")}</dd>
          </div>
        ) : null}
      </dl>
    </li>
  );
}
