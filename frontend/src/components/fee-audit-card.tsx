"use client";

/**
 * MDR & GST fee variance audit.
 *
 * Every rate, tolerance and label on this card comes from the audit response's
 * `policy` object. The card previously hardcoded "2.00% MDR + 18% GST" and
 * "Contractual MDR (2%)" into React, described the basis as generic
 * "contractual merchant rate cards" with no synthetic label, and formatted
 * money with `paise / 100` + `toFixed(2)` binary float arithmetic — which also
 * printed any negative variance as a literal zero.
 *
 * Money is now rendered by the shared exact-integer paise formatters, and the
 * basis is always named as the configured SYNTHETIC merchant policy it is.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { formatBps, formatCount, formatINR, formatSignedINR, shortHash } from "../lib/format";
import type { FeePolicyView } from "../lib/types";
import { IconBolt, IconSparkles, IconTrendingUp } from "./icons";

interface FeeAuditItem {
  record_id: string;
  record_type: string;
  gross_amount_paise: number;
  contractual_mdr_bps: number;
  contractual_gst_bps: number;
  expected_fee_paise: number;
  expected_gst_paise: number;
  expected_total_deduction_paise: number;
  actual_fee_paise: number;
  actual_gst_paise: number;
  actual_total_deduction_paise: number;
  variance_paise: number;
  is_anomaly: boolean;
  anomaly_reason: string | null;
}

interface FeeAuditSummary {
  run_id: string;
  total_gmv_paise: number;
  total_expected_fee_paise: number;
  total_actual_fee_paise: number;
  total_fee_variance_paise: number;
  total_expected_gst_paise: number;
  total_actual_gst_paise: number;
  total_gst_variance_paise: number;
  net_leakage_paise: number;
  audited_records_count: number;
  anomalous_records_count: number;
  items_returned_count: number;
  items_truncated: boolean;
  policy: FeePolicyView;
  items: FeeAuditItem[];
}

type LoadState = "IDLE" | "LOADING" | "READY" | "UNAVAILABLE" | "NOT_FOUND";

function isFeeAuditSummary(value: unknown, expectedRunId: string): value is FeeAuditSummary {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<FeeAuditSummary>;
  const policy = candidate.policy;
  return (
    candidate.run_id === expectedRunId &&
    typeof candidate.total_gmv_paise === "number" &&
    typeof candidate.net_leakage_paise === "number" &&
    typeof candidate.audited_records_count === "number" &&
    Array.isArray(candidate.items) &&
    typeof policy === "object" &&
    policy !== null &&
    typeof policy.policy_id === "string" &&
    typeof policy.policy_version === "string" &&
    Number.isInteger(policy.mdr_bps) &&
    Number.isInteger(policy.gst_on_fee_bps) &&
    Number.isInteger(policy.tolerance_paise)
  );
}

export function FeeAuditCard({ runId }: { runId: string | null }) {
  const [data, setData] = useState<FeeAuditSummary | null>(null);
  const [state, setState] = useState<LoadState>("IDLE");
  const [showDetails, setShowDetails] = useState(false);
  const requestId = useRef(0);

  const load = useCallback(async (targetRunId: string) => {
    const generation = ++requestId.current;
    setState("LOADING");
    setData(null);
    try {
      const response = await fetch(`/api/v1/runs/${encodeURIComponent(targetRunId)}/fee-audit`);
      if (generation !== requestId.current) return;
      if (response.status === 404) {
        setState("NOT_FOUND");
        return;
      }
      if (!response.ok) {
        setState("UNAVAILABLE");
        return;
      }
      const body: unknown = await response.json();
      if (generation !== requestId.current) return;
      if (!isFeeAuditSummary(body, targetRunId)) {
        // A response for another run, or a missing policy, is refused rather
        // than rendered with assumed rates.
        setState("UNAVAILABLE");
        return;
      }
      setData(body);
      setState("READY");
    } catch {
      if (generation === requestId.current) setState("UNAVAILABLE");
    }
  }, []);

  useEffect(() => {
    if (!runId) {
      requestId.current += 1;
      setData(null);
      setState("IDLE");
      return;
    }
    void load(runId);
  }, [runId, load]);

  const policy = data?.policy;

  return (
    <section
      aria-labelledby="fee-audit-heading"
      className="rounded-3xl border border-slate-200 bg-white p-6 shadow-xs text-slate-900"
    >
      <header className="flex flex-col gap-3 border-b border-slate-100 pb-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-slate-900 text-white shadow-xs">
            <IconTrendingUp size={20} className="text-white" />
          </span>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 id="fee-audit-heading" className="text-base font-bold text-slate-900">
                MDR &amp; GST variance audit
              </h3>
              {policy ? (
                <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10.5px] font-bold text-amber-900">
                  Synthetic policy · {formatBps(policy.mdr_bps)} MDR + {formatBps(policy.gst_on_fee_bps)} GST
                </span>
              ) : null}
            </div>
            <p className="mt-0.5 text-xs font-medium text-slate-500">
              Gateway deductions compared to the configured synthetic merchant agreement, in exact
              integer paise. Not Razorpay published pricing.
            </p>
          </div>
        </div>

        {data ? (
          <button
            type="button"
            onClick={() => setShowDetails((open) => !open)}
            aria-expanded={showDetails}
            className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 transition-colors hover:bg-slate-100"
          >
            {showDetails
              ? "Hide line items"
              : `Line items (${formatCount(data.items_returned_count)} of ${formatCount(data.audited_records_count)})`}
          </button>
        ) : null}
      </header>

      <div aria-live="polite">
        {state === "IDLE" && (
          <div className="py-8 text-center">
            <p className="text-sm font-bold text-slate-800">No run selected</p>
            <p className="mt-1 text-xs text-slate-500">
              A fee audit is scoped to one reconciliation run. Select or import a run first.
            </p>
          </div>
        )}

        {state === "LOADING" && (
          <div className="flex items-center justify-center gap-2 py-8 text-xs font-medium text-slate-500">
            <IconSparkles size={16} className="animate-spin text-slate-600" />
            <span>Auditing gateway deductions…</span>
          </div>
        )}

        {state === "NOT_FOUND" && (
          <div className="my-4 rounded-xl border border-slate-300 bg-slate-50 p-4">
            <p className="text-xs font-bold text-slate-950">This run no longer exists</p>
            <p className="mt-1 text-[11px] text-slate-600">
              The selected run could not be found, so no fee figures are shown. Open a current run
              from the dossier view.
            </p>
          </div>
        )}

        {state === "UNAVAILABLE" && runId && (
          <div role="alert" className="my-4 rounded-xl border border-slate-300 bg-slate-50 p-4">
            <p className="text-xs font-bold text-slate-950">Fee audit is unavailable</p>
            <p className="mt-1 text-[11px] text-slate-600">
              No figure is shown rather than a stale or unverified one.
            </p>
            <button
              type="button"
              onClick={() => void load(runId)}
              className="mt-3 rounded-lg border border-slate-900 bg-white px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-900 hover:bg-slate-50"
            >
              Retry audit
            </button>
          </div>
        )}
      </div>

      {state === "READY" && data && policy && (
        <div className="space-y-4 pt-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Tile
              label="Gross GMV audited"
              value={formatINR(data.total_gmv_paise)}
              sub={`${formatCount(data.audited_records_count)} payment records`}
            />
            <Tile
              label={`Expected MDR at ${formatBps(policy.mdr_bps)}`}
              value={formatINR(data.total_expected_fee_paise)}
              sub={`Actual ${formatINR(data.total_actual_fee_paise)}`}
            />
            <Tile
              label={`Expected GST at ${formatBps(policy.gst_on_fee_bps)}`}
              value={formatINR(data.total_expected_gst_paise)}
              sub={`Actual ${formatINR(data.total_actual_gst_paise)}`}
            />
            <Tile
              label="Net deduction variance"
              value={formatSignedINR(data.net_leakage_paise)}
              sub={
                data.net_leakage_paise === 0
                  ? "No deviation from policy"
                  : `${formatCount(data.anomalous_records_count)} record(s) outside tolerance`
              }
              tone={data.net_leakage_paise === 0 ? "positive" : "warning"}
            />
          </div>

          {data.anomalous_records_count > 0 && (
            <p className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50/60 p-3 text-xs font-medium text-amber-900">
              <IconBolt size={16} className="mt-px shrink-0 text-amber-600" />
              <span>
                <strong>{formatCount(data.anomalous_records_count)}</strong> record(s) deviate from
                the synthetic policy by more than its {formatINR(policy.tolerance_paise)} tolerance.
                A positive variance means the merchant was deducted more than the policy expects.
              </span>
            </p>
          )}

          {data.items_truncated && (
            <p className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-[11px] text-slate-600">
              Showing {formatCount(data.items_returned_count)} of{" "}
              {formatCount(data.audited_records_count)} audited records. The totals above cover the
              full audited population; the line items below are a page of it.
            </p>
          )}

          {showDetails && (
            <div className="overflow-x-auto rounded-2xl border border-slate-200">
              <table className="w-full border-collapse text-left text-xs">
                <caption className="sr-only">
                  Per-record expected and actual gateway deductions in integer paise
                </caption>
                <thead className="border-b border-slate-200 bg-slate-50 font-semibold text-slate-700">
                  <tr>
                    <th scope="col" className="px-3 py-2.5">Record</th>
                    <th scope="col" className="px-3 py-2.5">Gross</th>
                    <th scope="col" className="px-3 py-2.5">Expected fee + GST</th>
                    <th scope="col" className="px-3 py-2.5">Actual deducted</th>
                    <th scope="col" className="px-3 py-2.5 text-right">Variance</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 font-mono">
                  {data.items.map((item) => (
                    <tr
                      key={item.record_id}
                      className={item.is_anomaly ? "bg-amber-50/40" : "hover:bg-slate-50/50"}
                    >
                      <td className="px-3 py-2 font-bold text-slate-900">{item.record_id}</td>
                      <td className="px-3 py-2">{formatINR(item.gross_amount_paise)}</td>
                      <td className="px-3 py-2 text-slate-600">
                        {formatINR(item.expected_total_deduction_paise)}
                      </td>
                      <td className="px-3 py-2 text-slate-900">
                        {formatINR(item.actual_total_deduction_paise)}
                      </td>
                      <td
                        className={`px-3 py-2 text-right font-bold ${
                          item.variance_paise === 0
                            ? "text-emerald-700"
                            : item.variance_paise > 0
                              ? "text-amber-700"
                              : "text-slate-700"
                        }`}
                        title={item.anomaly_reason ?? undefined}
                      >
                        {formatSignedINR(item.variance_paise)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <footer className="flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-100 pt-3 font-mono text-[10px] text-slate-500">
            <span>run {data.run_id}</span>
            <span>policy {policy.policy_id}</span>
            <span>{policy.policy_version}</span>
            <span title={policy.policy_fingerprint}>
              basis {shortHash(policy.policy_fingerprint, 12)}
            </span>
            <span>tolerance {formatINR(policy.tolerance_paise)}</span>
            <span>{policy.rounding_rule.toLowerCase().replaceAll("_", " ")}</span>
          </footer>
          <p className="text-[10px] leading-relaxed text-slate-500">{policy.notice}</p>
        </div>
      )}
    </section>
  );
}

function Tile({
  label,
  value,
  sub,
  tone = "default",
}: {
  label: string;
  value: string;
  sub: string;
  tone?: "default" | "positive" | "warning";
}) {
  const toneClass =
    tone === "positive"
      ? "border-emerald-200 bg-emerald-50/40 text-emerald-900"
      : tone === "warning"
        ? "border-amber-200 bg-amber-50/40 text-amber-900"
        : "border-slate-200/80 bg-slate-50/50 text-slate-900";
  return (
    <div className={`rounded-2xl border p-3.5 ${toneClass}`}>
      <div className="text-[11px] font-semibold uppercase opacity-80">{label}</div>
      <div className="mt-1 font-mono text-lg font-bold tabular-nums">{value}</div>
      <div className="mt-0.5 text-[10.5px] font-medium opacity-75">{sub}</div>
    </div>
  );
}
