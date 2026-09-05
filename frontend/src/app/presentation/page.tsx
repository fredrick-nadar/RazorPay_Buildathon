"use client";

/**
 * ARGUS presentation mode (PRD 13.5.1 OPEN_PRESENTATION_MODE).
 *
 * Full-screen telemetry for ONE run. It honours the `?run=` selection the
 * control room links with, so presentation mode and the dashboard describe the
 * same batch; without one it falls back to the active run and says so.
 *
 * Every figure comes from that run's persisted summary. Nothing is hardcoded,
 * and the four states are distinct: loading, no run yet, unavailable, and
 * ready. The previous version always fetched the latest run, painted a red
 * "Backend unavailable" dot before the first fetch had resolved, and rendered
 * six em-dashes with no empty state when there was no run at all.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { formatCount, formatINR, formatRate, formatUtc, shortHash } from "../../lib/format";
import { requireRunView } from "../../lib/argus-selection";
import type { RunListItem } from "../../lib/types";

type LoadState = "LOADING" | "READY" | "EMPTY" | "UNAVAILABLE" | "NOT_FOUND";

interface RunSummary {
  eligible_record_count?: number;
  matched_record_count?: number;
  cases_count?: number;
  quarantined_row_count?: number;
  runtime_match_rate?: { numerator: number; denominator: number };
  financial_control_totals?: Record<string, number>;
  timing_metrics?: { records_per_second?: number; total_seconds?: number };
  mode?: string;
}

/** Read the run id the URL pins, if any. */
function pinnedRunId(): string | null {
  const raw = new URLSearchParams(window.location.search).get("run");
  return raw !== null && /^[A-Za-z0-9_-]{1,128}$/.test(raw) ? raw : null;
}

export default function PresentationPage() {
  const [run, setRun] = useState<RunListItem | null>(null);
  const [state, setState] = useState<LoadState>("LOADING");
  const [pinned, setPinned] = useState<string | null>(null);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const requestId = useRef(0);

  const load = useCallback(async (runId: string | null) => {
    const generation = ++requestId.current;
    try {
      const response = await fetch(
        runId ? `/api/v1/runs/${encodeURIComponent(runId)}/summary` : "/api/v1/runs/active",
      );
      if (generation !== requestId.current) return;
      if (response.status === 404) {
        setRun(null);
        setState("NOT_FOUND");
        return;
      }
      if (!response.ok) {
        setRun(null);
        setState("UNAVAILABLE");
        return;
      }
      const body: unknown = await response.json();
      if (generation !== requestId.current) return;
      if (body === null) {
        setRun(null);
        setState("EMPTY");
        setCheckedAt(new Date().toISOString());
        return;
      }
      // Refuse a response that does not describe the pinned run.
      setRun(requireRunView(body, runId));
      setState("READY");
      setCheckedAt(new Date().toISOString());
    } catch {
      if (generation !== requestId.current) return;
      setRun(null);
      setState("UNAVAILABLE");
    }
  }, []);

  useEffect(() => {
    const runId = pinnedRunId();
    setPinned(runId);
    void load(runId);
    // Refresh the same pinned run, never silently switching to a newer one.
    const timer = setInterval(() => void load(runId), 10_000);
    return () => clearInterval(timer);
  }, [load]);

  const summary = (run?.summary ?? {}) as RunSummary;
  const rate = summary.runtime_match_rate;
  const totals = summary.financial_control_totals ?? {};

  const metrics: Array<{ label: string; value: string; accent?: boolean }> = [
    { label: "Eligible records", value: formatCount(summary.eligible_record_count) },
    {
      label: "Runtime match rate",
      value: rate ? formatRate(rate.numerator, rate.denominator) : "—",
      accent: true,
    },
    { label: "Exception cases", value: formatCount(summary.cases_count) },
    {
      label: "Residual variance",
      value:
        totals.residual_abs_variance_paise !== undefined
          ? formatINR(totals.residual_abs_variance_paise)
          : "—",
    },
    {
      label: "Throughput",
      value:
        summary.timing_metrics?.records_per_second !== undefined
          ? `${formatCount(Math.round(summary.timing_metrics.records_per_second))} rec/s`
          : "—",
    },
    {
      label: "Integrity hash",
      value: run?.economic_output_hash ? shortHash(run.economic_output_hash, 16) : "—",
    },
  ];

  return (
    <div className="app-shell flex min-h-screen flex-col bg-[#08090b] text-zinc-200">
      <div className="app-backdrop flex min-h-screen flex-col px-10 py-8">
        <header className="flex shrink-0 flex-wrap items-center justify-between gap-4 border-b border-white/[0.06] pb-5">
          <div className="flex items-center gap-4">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-[#e6b45c]/25 bg-gradient-to-b from-[#e6b45c]/[0.14] to-transparent text-[#e6b45c]">
              <svg viewBox="0 0 42 34" fill="currentColor" aria-hidden="true" width="28" height="23">
                <polygon points="12,0 30,0 33.2,3.2 15.2,3.2" />
                <polygon points="14.6,5.6 32.6,5.6 35.8,8.8 17.8,8.8" />
                <polygon points="17.2,11.2 35.2,11.2 38.4,14.4 20.4,14.4" />
                <polygon points="3.2,16.8 21.2,16.8 24.4,20 6.4,20" />
                <polygon points="5.8,22.4 23.8,22.4 27,25.6 9,25.6" />
                <polygon points="8.4,28 26.4,28 29.6,31.2 11.6,31.2" />
              </svg>
            </span>
            <div>
              <h1 className="font-serif text-2xl font-bold italic tracking-tight text-zinc-50">
                Argus{" "}
                <span className="font-sans text-xs font-semibold not-italic tracking-[0.34em] text-zinc-500">
                  CONTROL
                </span>
              </h1>
              <p className="mt-1 text-[10px] font-medium uppercase tracking-[0.24em] text-zinc-600">
                Presentation mode · financial flight recorder
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 text-[11px]">
            <span className="inline-flex items-center gap-2 rounded-full border border-white/[0.08] bg-black/40 px-3 py-1.5 text-zinc-400">
              <span
                aria-hidden
                className={`h-1.5 w-1.5 rounded-full ${
                  state === "LOADING"
                    ? "bg-zinc-500"
                    : state === "READY" || state === "EMPTY"
                      ? "bg-emerald-400"
                      : state === "NOT_FOUND"
                        ? "bg-amber-400"
                      : "bg-rose-400"
                }`}
              />
              {state === "LOADING"
                ? "Checking backend…"
                : state === "READY" || state === "EMPTY"
                  ? "Backend reachable"
                  : state === "NOT_FOUND"
                    ? "Backend reachable · run not found"
                  : "Backend unavailable"}
            </span>
            <span className="rounded-full border border-white/[0.08] bg-black/40 px-3 py-1.5 text-zinc-500">
              Synthetic data only
            </span>
            <Link
              href={`/dashboard${run ? `?run=${encodeURIComponent(run.run_id)}` : ""}`}
              className="rounded-lg border border-white/[0.09] bg-white/[0.03] px-3.5 py-2 font-semibold text-zinc-200 transition-colors hover:bg-white/[0.06]"
            >
              Back to control room
            </Link>
          </div>
        </header>

        <main className="flex flex-1 flex-col justify-center py-10" aria-live="polite">
          {state === "LOADING" && (
            <p className="text-sm text-zinc-500">Loading the selected run…</p>
          )}

          {state === "EMPTY" && (
            <div className="max-w-xl">
              <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-[#e6b45c]">
                No run yet
              </p>
              <h2 className="mt-3 font-serif text-3xl font-bold italic text-zinc-100">
                Nothing has been reconciled
              </h2>
              <p className="mt-3 text-sm leading-relaxed text-zinc-500">
                The backend is reachable and holds no persisted run, so there is no measurement to
                present. Import gateway, bank and ledger evidence in the control room to create the
                first run.
              </p>
              <Link
                href="/dashboard"
                className="mt-6 inline-flex rounded-lg border border-[#e6b45c]/40 bg-[#e6b45c]/[0.08] px-4 py-2.5 text-xs font-semibold text-[#e6b45c] transition-colors hover:bg-[#e6b45c]/[0.14]"
              >
                Open the control room
              </Link>
            </div>
          )}

          {(state === "UNAVAILABLE" || state === "NOT_FOUND") && (
            <div role="alert" className="max-w-xl">
              <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-rose-400">
                {state === "NOT_FOUND" ? "Run not found" : "Unavailable"}
              </p>
              <h2 className="mt-3 font-serif text-3xl font-bold italic text-zinc-100">
                {state === "NOT_FOUND"
                  ? "That run no longer exists"
                  : "Run telemetry is unavailable"}
              </h2>
              <p className="mt-3 text-sm leading-relaxed text-zinc-500">
                {state === "NOT_FOUND"
                  ? `Run ${pinned ?? ""} could not be read, so no figure is shown for it.`
                  : "The backend did not answer. No previously loaded figure is presented as current."}
              </p>
              <div className="mt-6 flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={() => void load(pinned)}
                  className="rounded-lg border border-white/[0.12] bg-white/[0.04] px-4 py-2.5 text-xs font-semibold text-zinc-200 transition-colors hover:bg-white/[0.08]"
                >
                  Retry
                </button>
                {pinned && (
                  <button
                    type="button"
                    onClick={() => window.location.assign("/presentation")}
                    className="rounded-lg border border-white/[0.12] bg-transparent px-4 py-2.5 text-xs font-semibold text-zinc-400 transition-colors hover:bg-white/[0.06]"
                  >
                    Show the active run instead
                  </button>
                )}
              </div>
            </div>
          )}

          {state === "READY" && run && (
            <>
              <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-[#e6b45c]">
                {pinned ? "Selected batch" : "Active batch"}
              </p>
              <p className="mt-2 select-all font-mono text-sm text-zinc-500">
                {run.run_id} · {summary.mode ?? "—"} · {run.status.toLowerCase()}
              </p>

              <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {metrics.map((metric) => (
                  <div
                    key={metric.label}
                    className="rounded-2xl border border-white/[0.07] bg-[#101013]/90 px-6 py-6"
                  >
                    <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
                      {metric.label}
                    </div>
                    <div
                      className={`mt-3 truncate font-mono text-4xl font-bold tabular-nums tracking-tight ${
                        metric.accent ? "text-emerald-300" : "text-zinc-100"
                      }`}
                    >
                      {metric.value}
                    </div>
                  </div>
                ))}
              </div>

              {summary.cases_count === 0 && (
                <p className="mt-6 max-w-2xl rounded-2xl border border-emerald-400/20 bg-emerald-400/[0.06] px-5 py-4 text-sm text-emerald-200">
                  This run raised <strong>no exception cases</strong>. A zero-exception result is a
                  clean reconciliation, not a missing measurement.
                </p>
              )}
            </>
          )}
        </main>

        <footer className="shrink-0 border-t border-white/[0.06] pt-4">
          <p className="text-[10px] leading-relaxed text-zinc-600">
            Values come from the persisted run named above · synthetic demo data only · the match
            rate is that run&rsquo;s runtime self-report, not evaluator accuracy · voice can never
            approve or apply corrections
            {checkedAt ? ` · read ${formatUtc(checkedAt)}` : ""}
          </p>
        </footer>
      </div>
    </div>
  );
}
