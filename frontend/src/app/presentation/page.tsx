"use client";

/**
 * ARGUS presentation mode (PRD 13.5.1 OPEN_PRESENTATION_MODE).
 * Fixed in-app route for the five-minute demo: full-screen flight recorder
 * telemetry from the latest persisted run — no hard-coded financial numbers.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

interface RunListItem {
  run_id: string;
  status: string;
  summary: {
    eligible_record_count?: number;
    matched_record_count?: number;
    cases_count?: number;
    runtime_match_rate?: { numerator: number; denominator: number };
    financial_control_totals?: Record<string, number>;
    timing_metrics?: { records_per_second?: number; total_seconds?: number };
    economic_output_hash?: string;
    mode?: string;
  };
}

function formatINR(paise: number): string {
  const negative = paise < 0;
  const abs = Math.abs(paise);
  return `${negative ? "\u2212" : ""}\u20B9${Math.floor(abs / 100).toLocaleString("en-IN")}.${(abs % 100).toString().padStart(2, "0")}`;
}

export default function PresentationPage() {
  const [run, setRun] = useState<RunListItem | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/runs/active");
      if (!res.ok) throw new Error(String(res.status));
      setRun((await res.json()) as RunListItem | null);
      setOnline(true);
    } catch {
      setOnline(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 10000);
    return () => clearInterval(timer);
  }, [load]);

  const summary = run?.summary;
  const rate = summary?.runtime_match_rate;
  const matchRate =
    rate && rate.denominator > 0
      ? `${((rate.numerator / rate.denominator) * 100).toFixed(1)}%`
      : "\u2014";
  const totals = summary?.financial_control_totals ?? {};

  const metrics: Array<{ label: string; value: string; accent?: boolean }> = [
    {
      label: "Eligible records",
      value: summary?.eligible_record_count?.toLocaleString("en-IN") ?? "\u2014",
    },
    { label: "Deterministic match rate", value: matchRate, accent: true },
    { label: "Exception cases", value: summary?.cases_count?.toLocaleString("en-IN") ?? "\u2014" },
    {
      label: "Residual variance",
      value:
        totals.residual_abs_variance_paise !== undefined
          ? formatINR(totals.residual_abs_variance_paise)
          : "\u2014",
    },
    {
      label: "Throughput",
      value:
        summary?.timing_metrics?.records_per_second !== undefined
          ? `${Math.round(summary.timing_metrics.records_per_second).toLocaleString("en-IN")} rec/s`
          : "\u2014",
    },
    {
      label: "Integrity hash",
      value: summary?.economic_output_hash
        ? `${summary.economic_output_hash.slice(0, 16)}\u2026`
        : "\u2014",
    },
  ];

  return (
    <div className="app-shell flex min-h-screen flex-col bg-[#08090b] text-zinc-200">
      <div className="app-backdrop flex min-h-screen flex-col px-10 py-8">
        <header className="flex shrink-0 items-center justify-between border-b border-white/[0.06] pb-5">
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
          <div className="flex items-center gap-3 text-[11px]">
            <span className="inline-flex items-center gap-2 rounded-full border border-white/[0.08] bg-black/40 px-3 py-1.5 text-zinc-400">
              <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${online ? "bg-emerald-400" : "bg-rose-400"}`} />
              {online ? "Backend reachable" : "Backend unavailable"}
            </span>
            <span className="rounded-full border border-white/[0.08] bg-black/40 px-3 py-1.5 text-zinc-500">
              Synthetic data only
            </span>
            <Link
              href="/dashboard"
              className="rounded-lg border border-white/[0.09] bg-white/[0.03] px-3.5 py-2 font-semibold text-zinc-200 transition-colors hover:bg-white/[0.06]"
            >
              Back to control room
            </Link>
          </div>
        </header>

        <main className="flex flex-1 flex-col justify-center py-10">
          <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-[#e6b45c]">
            Latest batch
          </p>
          <p className="mt-2 select-all font-mono text-sm text-zinc-500">
            {run?.run_id ?? "\u2014"} · {summary?.mode ?? "\u2014"} · {run?.status.toLowerCase() ?? "\u2014"}
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
        </main>

        <footer className="shrink-0 border-t border-white/[0.06] pt-4">
          <p className="text-[10px] leading-relaxed text-zinc-600">
            Say &ldquo;open presentation mode&rdquo; from the voice copilot to return here · values come
            from the latest persisted run · voice can never approve or apply corrections.
          </p>
        </footer>
      </div>
    </div>
  );
}
