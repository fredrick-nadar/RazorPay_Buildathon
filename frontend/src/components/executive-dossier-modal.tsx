"use client";

import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";

import { formatCount, formatINR, formatRate, humanizeEnum, shortHash } from "../lib/format";
import {
  IconCheck,
  IconCopy,
  IconDownload,
  IconPrinter,
  IconShield,
  IconX,
} from "./icons";

interface DossierCase {
  case_id: string;
  category: string;
  status: string;
  variance_paise: number;
  affected_amount_paise: number;
  proposed_delta_paise: number | null;
  summary: string;
  proof: {
    proof_id: string;
    verifier_status: string;
    verifier_rule_id: string;
    authority_decision: string;
  } | null;
  opened_at_utc: string;
}

interface RuntimeMetrics {
  eligible_record_count: number | null;
  matched_record_count: number | null;
  runtime_match_rate: { numerator: number; denominator: number; note?: string } | null;
  case_status_counts: Record<string, number>;
  verifier_status_counts: Record<string, number>;
  proof_count: number;
  audit_event_count: number;
}

interface DossierData {
  run_id: string;
  tenant_id: string;
  status: string;
  started_at_utc: string;
  finished_at_utc: string | null;
  economic_output_hash: string | null;
  dossier_digest: string;
  digest_algorithm: string;
  digest_scope: string[];
  cases_count: number;
  total_abs_case_variance_paise: number;
  runtime_metrics: RuntimeMetrics;
  cases: DossierCase[];
  audit_trail: Array<{
    event_id: string;
    action: string;
    case_id: string | null;
    actor: string;
    timestamp_utc: string;
    digest: string;
  }>;
  provenance: {
    scope: "ACTIVE_RUN_RUNTIME";
    data_classification: "SYNTHETIC_ONLY";
    evaluator_labels_used: false;
    external_audit_performed: false;
    regulatory_certification: false;
    money_representation: "SIGNED_INTEGER_PAISE";
    source_rows_immutable: boolean;
    notice: string;
  };
}

interface ExecutiveDossierModalProps {
  open: boolean;
  onClose: () => void;
  runId: string | null;
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function runtimeMatchRate(metrics: RuntimeMetrics): string {
  const rate = metrics.runtime_match_rate;
  return rate ? formatRate(rate.numerator, rate.denominator) : "—";
}

function printRunDossier(data: DossierData) {
  const printWindow = window.open("", "_blank", "width=940,height=1100");
  if (!printWindow) return;

  const caseRows = data.cases.length
    ? data.cases
        .map(
          (item) => `<tr>
  <td class="mono">${escapeHtml(item.case_id)}</td>
  <td>${escapeHtml(humanizeEnum(item.category))}</td>
  <td class="mono">${escapeHtml(formatINR(item.variance_paise))}</td>
  <td>${item.proof ? `${escapeHtml(item.proof.verifier_rule_id)} · ${escapeHtml(item.proof.verifier_status)}` : "No proof recorded"}</td>
  <td>${escapeHtml(humanizeEnum(item.status))}</td>
</tr>`,
        )
        .join("")
    : '<tr><td colspan="5" class="empty">No exception cases were recorded for this run.</td></tr>';

  const auditRows = data.audit_trail.length
    ? data.audit_trail
        .map(
          (item) => `<tr>
  <td class="mono">${escapeHtml(item.action)}</td>
  <td>${escapeHtml(item.actor)}</td>
  <td class="mono">${escapeHtml(item.case_id ?? "Run level")}</td>
  <td class="mono">${escapeHtml(item.timestamp_utc)}</td>
</tr>`,
        )
        .join("")
    : '<tr><td colspan="4" class="empty">No audit events were recorded.</td></tr>';

  const html = `<!doctype html>
<html><head><meta charset="utf-8"><title>ARGUS_RUN_EVIDENCE_${escapeHtml(data.run_id)}</title>
<style>
  @page { size: A4; margin: 13mm; }
  * { box-sizing: border-box; }
  body { margin: 0; color: #111827; font-family: Arial, sans-serif; font-size: 10px; }
  header { display: flex; justify-content: space-between; gap: 24px; border-bottom: 2px solid #111827; padding-bottom: 13px; }
  h1 { margin: 0; font-size: 18px; letter-spacing: -.02em; }
  .eyebrow { color: #64748b; font-size: 8px; font-weight: 700; letter-spacing: .15em; text-transform: uppercase; }
  .scope { border: 1px solid #cbd5e1; padding: 7px 10px; text-align: right; }
  .digest { margin: 14px 0; background: #111827; color: white; padding: 12px; }
  .digest strong { display: block; margin-top: 4px; font-family: monospace; overflow-wrap: anywhere; }
  .notice { margin-top: 6px; color: #cbd5e1; }
  .metrics { display: grid; grid-template-columns: repeat(4, 1fr); border: 1px solid #cbd5e1; }
  .metric { padding: 10px; border-right: 1px solid #cbd5e1; }
  .metric:last-child { border-right: 0; }
  .metric b { display: block; margin-top: 4px; font-family: monospace; font-size: 17px; }
  h2 { margin: 18px 0 7px; font-size: 10px; letter-spacing: .09em; text-transform: uppercase; }
  table { width: 100%; border-collapse: collapse; }
  th { color: #475569; background: #f1f5f9; text-align: left; text-transform: uppercase; font-size: 8px; }
  th, td { padding: 7px; border: 1px solid #e2e8f0; vertical-align: top; }
  .mono { font-family: monospace; }
  .empty { color: #64748b; text-align: center; padding: 14px; }
  footer { margin-top: 18px; border-top: 1px solid #cbd5e1; padding-top: 8px; color: #64748b; display: flex; justify-content: space-between; gap: 20px; }
</style></head><body>
<header>
  <div><div class="eyebrow">ARGUS CONTROL · FINANCIAL FLIGHT RECORDER</div><h1>Run Evidence Dossier</h1></div>
  <div class="scope"><b>RUNTIME EVIDENCE</b><br>SYNTHETIC DATA ONLY</div>
</header>
<div class="digest">
  <span>${escapeHtml(data.digest_algorithm)} dossier export digest</span>
  <strong>${escapeHtml(data.dossier_digest)}</strong>
  <div class="notice">Internal consistency digest only · not an external audit or regulatory certificate</div>
</div>
<div class="metrics">
  <div class="metric"><span>Eligible records</span><b>${formatCount(data.runtime_metrics.eligible_record_count)}</b></div>
  <div class="metric"><span>Matched records</span><b>${formatCount(data.runtime_metrics.matched_record_count)}</b></div>
  <div class="metric"><span>Runtime match rate</span><b>${runtimeMatchRate(data.runtime_metrics)}</b></div>
  <div class="metric"><span>Exception cases</span><b>${formatCount(data.cases_count)}</b></div>
</div>
<h2>Exception cases and latest verifier results</h2>
<table><thead><tr><th>Case ID</th><th>Category</th><th>Signed variance</th><th>Latest verifier result</th><th>Current case status</th></tr></thead><tbody>${caseRows}</tbody></table>
<h2>Recorded audit events (${data.runtime_metrics.audit_event_count})</h2>
<table><thead><tr><th>Action</th><th>Actor</th><th>Scope</th><th>Timestamp UTC</th></tr></thead><tbody>${auditRows}</tbody></table>
<footer>
  <span>Run ${escapeHtml(data.run_id)} · ${escapeHtml(data.status)}</span>
  <span>${escapeHtml(data.provenance.notice)}</span>
</footer>
</body></html>`;

  printWindow.document.open();
  printWindow.document.write(html);
  printWindow.document.close();
  printWindow.focus();
  window.setTimeout(() => printWindow.print(), 250);
}

function CountChip({ label, value }: { label: string; value: number | undefined }) {
  if (!value) return null;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2 py-1 text-[10px] text-slate-600">
      <b className="font-mono text-slate-950">{value}</b>
      {label}
    </span>
  );
}

export function ExecutiveDossierModal({ open, onClose, runId }: ExecutiveDossierModalProps) {
  const [data, setData] = useState<DossierData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open || !runId) return;
    const controller = new AbortController();
    setData(null);
    setLoading(true);
    setError(null);

    void fetch(`/api/v1/runs/${encodeURIComponent(runId)}/dossier`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Dossier request failed (${response.status})`);
        const result = (await response.json()) as DossierData;
        if (result.run_id !== runId) throw new Error("Dossier identity did not match the active run");
        setData(result);
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "Unable to load the run dossier");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [open, runId]);

  const caseStatusEntries = useMemo(
    () => (data ? Object.entries(data.runtime_metrics.case_status_counts) : []),
    [data],
  );

  const copySummary = () => {
    if (!data) return;
    const metrics = data.runtime_metrics;
    const text = `ARGUS RUN EVIDENCE DOSSIER
Run ID: ${data.run_id}
Status: ${data.status}
Eligible records: ${formatCount(metrics.eligible_record_count)}
Matched records: ${formatCount(metrics.matched_record_count)}
Runtime match rate: ${runtimeMatchRate(metrics)}
Exception cases: ${formatCount(data.cases_count)}
Total absolute case variance: ${formatINR(data.total_abs_case_variance_paise)}
${data.digest_algorithm} dossier digest: ${data.dossier_digest}
Scope: active-run runtime evidence; synthetic data only
Notice: ${data.provenance.notice}`;

    void navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  const downloadJson = () => {
    if (!data) return;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `argus-run-evidence-${data.run_id}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  if (!open) return null;

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto p-3 sm:p-6">
        <motion.button
          type="button"
          aria-label="Close dossier"
          className="fixed inset-0 bg-slate-950/55 backdrop-blur-[2px]"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        />

        <motion.section
          role="dialog"
          aria-modal="true"
          aria-labelledby="run-dossier-title"
          className="relative z-10 max-h-[92vh] w-full max-w-5xl overflow-y-auto rounded-[22px] border border-slate-200 bg-[#fbfcfd] shadow-2xl"
          initial={{ opacity: 0, y: 12, scale: 0.985 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 8, scale: 0.99 }}
          transition={{ duration: 0.18, ease: "easeOut" }}
        >
          <header className="sticky top-0 z-20 flex items-center justify-between gap-4 border-b border-slate-200 bg-white/95 px-5 py-4 backdrop-blur-md sm:px-6">
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-slate-950 text-white">
                <IconShield size={16} />
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h2 id="run-dossier-title" className="text-[15px] font-semibold tracking-tight text-slate-950">
                    Run evidence dossier
                  </h2>
                  <span className="hidden rounded-full border border-slate-300 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.12em] text-slate-600 sm:inline-flex">
                    Runtime evidence
                  </span>
                </div>
                <p className="truncate font-mono text-[10px] text-slate-500">{runId ?? "No active run"}</p>
              </div>
            </div>

            <div className="flex items-center gap-1.5">
              <button type="button" onClick={copySummary} disabled={!data} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-40">
                {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}
                <span className="hidden sm:inline">{copied ? "Copied" : "Copy"}</span>
              </button>
              <button type="button" onClick={downloadJson} disabled={!data} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-[11px] font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-40">
                <IconDownload size={13} /><span className="hidden sm:inline">JSON</span>
              </button>
              <button type="button" onClick={() => data && printRunDossier(data)} disabled={!data} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-slate-950 px-3 text-[11px] font-semibold text-white transition hover:bg-slate-800 disabled:opacity-40">
                <IconPrinter size={13} /><span className="hidden sm:inline">Print</span>
              </button>
              <button type="button" onClick={onClose} aria-label="Close dialog" className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-950">
                <IconX size={15} />
              </button>
            </div>
          </header>

          {loading && (
            <div className="flex min-h-72 items-center justify-center">
              <div className="flex items-center gap-2 text-xs text-slate-500">
                <span className="h-2 w-2 animate-pulse rounded-full bg-slate-900" />
                Loading persisted run evidence…
              </div>
            </div>
          )}

          {error && !loading && (
            <div className="m-6 rounded-xl border border-slate-300 bg-white p-5 text-sm text-slate-700">
              <p className="font-semibold text-slate-950">The dossier could not be loaded.</p>
              <p className="mt-1 text-xs text-slate-500">{error}</p>
            </div>
          )}

          {data && !loading && (
            <div className="space-y-5 p-5 sm:p-6">
              <section className="rounded-2xl bg-slate-950 px-5 py-4 text-white">
                <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
                  <div className="min-w-0">
                    <p className="text-[9px] font-semibold uppercase tracking-[0.18em] text-slate-400">
                      {data.digest_algorithm} dossier export digest
                    </p>
                    <p className="mt-1 break-all font-mono text-[12px] text-slate-100">{data.dossier_digest}</p>
                  </div>
                  <div className="shrink-0 text-left sm:text-right">
                    <p className="text-[9px] uppercase tracking-[0.14em] text-slate-500">Run state</p>
                    <p className="mt-1 font-mono text-xs font-semibold">{humanizeEnum(data.status)}</p>
                  </div>
                </div>
                <p className="mt-3 border-t border-white/10 pt-3 text-[10px] leading-relaxed text-slate-400">
                  Internal consistency digest only. It binds the listed run fields; it is not an external audit or regulatory certificate.
                </p>
              </section>

              <section className="grid grid-cols-2 overflow-hidden rounded-2xl border border-slate-200 bg-white sm:grid-cols-4">
                {[
                  ["Eligible records", formatCount(data.runtime_metrics.eligible_record_count)],
                  ["Matched records", formatCount(data.runtime_metrics.matched_record_count)],
                  ["Runtime match rate", runtimeMatchRate(data.runtime_metrics)],
                  ["Exception cases", formatCount(data.cases_count)],
                ].map(([label, value], index) => (
                  <div key={label} className={`p-4 ${index % 2 === 0 ? "border-r" : ""} border-slate-200 sm:border-r sm:last:border-r-0`}>
                    <p className="text-[9px] font-semibold uppercase tracking-[0.12em] text-slate-500">{label}</p>
                    <p className="mt-1 font-mono text-xl font-semibold tabular-nums text-slate-950">{value}</p>
                  </div>
                ))}
              </section>

              <section className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
                <span className="mr-1 text-[9px] font-semibold uppercase tracking-[0.14em] text-slate-500">Current case states</span>
                {caseStatusEntries.length ? caseStatusEntries.map(([status, count]) => (
                  <CountChip key={status} label={humanizeEnum(status)} value={count} />
                )) : <span className="text-[11px] text-slate-500">No cases recorded</span>}
                <span className="ml-auto font-mono text-[10px] text-slate-500">
                  abs. case variance {formatINR(data.total_abs_case_variance_paise)}
                </span>
              </section>

              <section>
                <div className="mb-2 flex items-end justify-between gap-4">
                  <div>
                    <h3 className="text-xs font-semibold text-slate-950">Exception evidence</h3>
                    <p className="mt-0.5 text-[10px] text-slate-500">Latest persisted verifier result per case; unresolved evidence remains unresolved.</p>
                  </div>
                  <span className="font-mono text-[10px] text-slate-500">{data.runtime_metrics.proof_count} proofs recorded</span>
                </div>
                <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
                  <table className="w-full min-w-[760px] border-collapse text-left text-[11px]">
                    <thead className="border-b border-slate-200 bg-slate-50 text-[9px] uppercase tracking-[0.1em] text-slate-500">
                      <tr><th className="px-3 py-2.5">Case</th><th className="px-3 py-2.5">Category</th><th className="px-3 py-2.5">Signed variance</th><th className="px-3 py-2.5">Verifier</th><th className="px-3 py-2.5">Current status</th></tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {data.cases.length ? data.cases.map((item) => (
                        <tr key={item.case_id} className="align-top hover:bg-slate-50/70">
                          <td className="px-3 py-2.5 font-mono font-semibold text-slate-950">{item.case_id}</td>
                          <td className="px-3 py-2.5 text-slate-600">{humanizeEnum(item.category)}</td>
                          <td className="px-3 py-2.5 font-mono font-semibold text-slate-900">{formatINR(item.variance_paise)}</td>
                          <td className="px-3 py-2.5 text-slate-600">{item.proof ? <><span className="font-mono text-slate-900">{item.proof.verifier_rule_id}</span><br/><span className="text-[10px]">{humanizeEnum(item.proof.verifier_status)}</span></> : "No proof recorded"}</td>
                          <td className="px-3 py-2.5"><span className="rounded-md border border-slate-200 bg-slate-50 px-1.5 py-1 text-[9px] font-semibold uppercase tracking-wide text-slate-700">{humanizeEnum(item.status)}</span></td>
                        </tr>
                      )) : (
                        <tr><td colSpan={5} className="px-3 py-8 text-center text-xs text-slate-500">No exception cases were recorded for this run.</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </section>

              <section>
                <div className="mb-2 flex items-end justify-between gap-4">
                  <div><h3 className="text-xs font-semibold text-slate-950">Append-only audit trail</h3><p className="mt-0.5 text-[10px] text-slate-500">Recorded system and authority events for this run.</p></div>
                  <span className="font-mono text-[10px] text-slate-500">{data.runtime_metrics.audit_event_count} events</span>
                </div>
                <div className="max-h-44 space-y-1 overflow-y-auto rounded-xl border border-slate-200 bg-white p-2">
                  {data.audit_trail.length ? data.audit_trail.map((item) => (
                    <div key={item.event_id} className="grid grid-cols-[1fr_auto] gap-3 rounded-lg px-2.5 py-2 text-[10px] hover:bg-slate-50">
                      <div className="min-w-0"><span className="font-mono font-semibold text-slate-900">{item.action}</span><span className="mx-1.5 text-slate-300">/</span><span className="text-slate-500">{item.actor}</span>{item.case_id && <span className="ml-2 font-mono text-slate-500">{item.case_id}</span>}</div>
                      <time className="font-mono text-slate-400">{item.timestamp_utc}</time>
                    </div>
                  )) : <p className="py-5 text-center text-xs text-slate-500">No audit events recorded.</p>}
                </div>
              </section>

              <footer className="flex flex-col justify-between gap-2 border-t border-slate-200 pt-4 text-[10px] leading-relaxed text-slate-500 sm:flex-row">
                <p className="max-w-2xl">{data.provenance.notice}</p>
                <p className="shrink-0 font-mono">output {shortHash(data.economic_output_hash, 18) || "digest unavailable"}</p>
              </footer>
            </div>
          )}
        </motion.section>
      </div>
    </AnimatePresence>
  );
}
