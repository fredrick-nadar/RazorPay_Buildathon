"use client";

import { useEffect, useState } from "react";
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
  items: FeeAuditItem[];
}

interface FeeAuditCardProps {
  runId: string | null;
}

export function FeeAuditCard({ runId }: FeeAuditCardProps) {
  const [data, setData] = useState<FeeAuditSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(false);

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const res = await fetch(`/api/v1/runs/${encodeURIComponent(runId)}/fee-audit`);
        if (!res.ok) {
          throw new Error("Failed to load fee audit summary");
        }
        const json = await res.json();
        setData(json);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Error loading fee audit");
      } finally {
        setLoading(false);
      }
    })();
  }, [runId]);

  if (!runId) return null;

  return (
    <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-xs text-slate-900">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-slate-100">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-slate-900 text-white shadow-xs">
            <IconTrendingUp size={20} className="text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold text-slate-900">
                MDR & GST Pricing Reconciler
              </h3>
              <span className="rounded-full bg-slate-100 border border-slate-200 px-2 py-0.5 text-[10.5px] font-bold text-slate-700">
                2.00% MDR + 18% GST
              </span>
            </div>
            <p className="text-xs text-slate-500 font-medium mt-0.5">
              Automated gateway deduction audit against contractual merchant rate cards down to exact integer paise.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {data && (
            <button
              type="button"
              onClick={() => setShowDetails(!showDetails)}
              className="px-3 py-1.5 rounded-xl border border-slate-200 bg-slate-50 hover:bg-slate-100 text-xs font-semibold text-slate-700 transition-colors"
            >
              {showDetails ? "Hide Line Items" : `View Audited Records (${data.audited_records_count})`}
            </button>
          )}
        </div>
      </div>

      {loading && (
        <div className="py-8 flex items-center justify-center gap-2 text-slate-400 text-xs font-medium">
          <IconSparkles size={16} className="animate-spin text-slate-600" />
          <span>Auditing payment gateway deductions...</span>
        </div>
      )}

      {error && (
        <div className="py-4 text-xs text-slate-500 text-center font-medium">
          {error}
        </div>
      )}

      {data && !loading && (
        <div className="pt-4 space-y-4">
          {/* Key Metrics Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="p-3.5 rounded-2xl border border-slate-200/80 bg-slate-50/50">
              <div className="text-[11px] font-semibold text-slate-500 uppercase">Gross GMV Audited</div>
              <div className="text-xl font-bold text-slate-900 mt-1 font-mono">
                ₹{(data.total_gmv_paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
              </div>
              <div className="text-[10.5px] text-slate-500 font-medium mt-0.5">
                {data.audited_records_count} transactions
              </div>
            </div>

            <div className="p-3.5 rounded-2xl border border-slate-200/80 bg-slate-50/50">
              <div className="text-[11px] font-semibold text-slate-500 uppercase">Contractual MDR (2%)</div>
              <div className="text-xl font-bold text-slate-900 mt-1 font-mono">
                ₹{(data.total_expected_fee_paise / 100).toFixed(2)}
              </div>
              <div className="text-[10.5px] text-slate-500 font-medium mt-0.5">
                Actual: ₹{(data.total_actual_fee_paise / 100).toFixed(2)}
              </div>
            </div>

            <div className="p-3.5 rounded-2xl border border-slate-200/80 bg-slate-50/50">
              <div className="text-[11px] font-semibold text-slate-500 uppercase">GST on Fees (18%)</div>
              <div className="text-xl font-bold text-slate-900 mt-1 font-mono">
                ₹{(data.total_expected_gst_paise / 100).toFixed(2)}
              </div>
              <div className="text-[10.5px] text-slate-500 font-medium mt-0.5">
                Actual: ₹{(data.total_actual_gst_paise / 100).toFixed(2)}
              </div>
            </div>

            <div className={`p-3.5 rounded-2xl border ${
              data.net_leakage_paise === 0
                ? "border-emerald-200 bg-emerald-50/40 text-emerald-900"
                : "border-amber-200 bg-amber-50/40 text-amber-900"
            }`}>
              <div className="text-[11px] font-semibold uppercase opacity-80">Net Leakage Variance</div>
              <div className="text-xl font-black mt-1 font-mono">
                {data.net_leakage_paise === 0 ? "₹0.00" : `₹${(Math.abs(data.net_leakage_paise) / 100).toFixed(2)}`}
              </div>
              <div className="text-[10.5px] font-semibold mt-0.5">
                {data.net_leakage_paise === 0 ? "Zero Fee Drift" : `${data.anomalous_records_count} Anomaly Flagged`}
              </div>
            </div>
          </div>

          {/* Anomaly Callout */}
          {data.anomalous_records_count > 0 && (
            <div className="p-3 rounded-xl border border-amber-200 bg-amber-50/60 flex items-center justify-between text-xs text-amber-900 font-medium">
              <div className="flex items-center gap-2">
                <IconBolt size={16} className="text-amber-600 shrink-0" />
                <span>
                  <strong>MDR Rate Alert:</strong> {data.anomalous_records_count} transaction(s) have micro-deduction variances exceeding standard rounding thresholds.
                </span>
              </div>
            </div>
          )}

          {/* Line Items Table Preview */}
          {showDetails && (
            <div className="overflow-x-auto rounded-2xl border border-slate-200">
              <table className="w-full text-left text-xs border-collapse font-mono">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50 font-sans font-semibold text-slate-700">
                    <th className="py-2.5 px-3">Payment ID</th>
                    <th className="py-2.5 px-3">Gross Amount</th>
                    <th className="py-2.5 px-3">Expected Fee + Tax</th>
                    <th className="py-2.5 px-3">Actual Deducted</th>
                    <th className="py-2.5 px-3 text-right">Variance</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.items.map((item) => (
                    <tr key={item.record_id} className={item.is_anomaly ? "bg-amber-50/40" : "hover:bg-slate-50/50"}>
                      <td className="py-2 px-3 font-bold text-slate-900">{item.record_id}</td>
                      <td className="py-2 px-3">₹{(item.gross_amount_paise / 100).toFixed(2)}</td>
                      <td className="py-2 px-3 text-slate-600">₹{(item.expected_total_deduction_paise / 100).toFixed(2)}</td>
                      <td className="py-2 px-3 text-slate-900">₹{(item.actual_total_deduction_paise / 100).toFixed(2)}</td>
                      <td className={`py-2 px-3 text-right font-bold ${item.variance_paise !== 0 ? "text-amber-600" : "text-emerald-600"}`}>
                        {item.variance_paise > 0 ? `+₹${(item.variance_paise / 100).toFixed(2)}` : "₹0.00"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
