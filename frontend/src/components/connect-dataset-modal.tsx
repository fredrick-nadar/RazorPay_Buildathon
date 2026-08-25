"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  IconBolt,
  IconCheck,
  IconLayers,
  IconPlug,
  IconRazorpay,
  IconShield,
  IconSparkles,
  IconX,
} from "./icons";

interface ConnectDatasetModalProps {
  open: boolean;
  onClose: () => void;
  onRunSynthetic: (profile: "dev" | "adversarial", mode: "rules-only" | "agent") => void;
  onSyncSuccess: (runId: string, summary: Record<string, unknown> | null) => void;
}

export function ConnectDatasetModal({
  open,
  onClose,
  onRunSynthetic,
  onSyncSuccess,
}: ConnectDatasetModalProps) {
  const [selectedSource, setSelectedSource] = useState<"synthetic" | "razorpay">("synthetic");
  const [status, setStatus] = useState<{
    configured: boolean;
    key_id_masked: string | null;
    base_url: string;
  } | null>(null);

  const [customKeyId, setCustomKeyId] = useState("");
  const [customKeySecret, setCustomKeySecret] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<{
    payments_count: number;
    refunds_count: number;
    settlements_count: number;
  } | null>(null);

  useEffect(() => {
    if (!open) return;
    void (async () => {
      try {
        const res = await fetch("/api/v1/razorpay/status");
        if (res.ok) {
          const data = await res.json();
          setStatus(data);
        }
      } catch {
        /* best effort */
      }
    })();
  }, [open]);

  async function handleRazorpaySync() {
    setSyncing(true);
    setSyncError(null);
    setSyncResult(null);
    try {
      const res = await fetch("/api/v1/razorpay/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          key_id: customKeyId.trim() || undefined,
          key_secret: customKeySecret.trim() || undefined,
          count: 25,
          auto_reconcile: true,
        }),
      });

      if (!res.ok) {
        let errMsg = "Failed to sync with Razorpay API";
        try {
          const err = await res.json();
          errMsg = err.detail || errMsg;
        } catch {
          /* ignore non-json error */
        }
        throw new Error(errMsg);
      }

      const data = await res.json();
      setSyncResult({
        payments_count: data.payments_count,
        refunds_count: data.refunds_count,
        settlements_count: data.settlements_count,
      });

      if (data.run_id) {
        setTimeout(() => {
          onSyncSuccess(data.run_id, data.summary);
          onClose();
        }, 1200);
      }
    } catch (e: unknown) {
      setSyncError(e instanceof Error ? e.message : "Network error while connecting to Razorpay");
    } finally {
      setSyncing(false);
    }
  }

  if (!open) return null;

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        {/* Backdrop */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
          className="fixed inset-0 bg-slate-900/40 backdrop-blur-xs transition-opacity"
        />

        {/* Modal Container */}
        <motion.div
          initial={{ opacity: 0, scale: 0.95, y: 15 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: 15 }}
          transition={{ type: "spring", stiffness: 380, damping: 28 }}
          className="relative w-full max-w-2xl overflow-hidden rounded-3xl border border-slate-200 bg-white p-6 sm:p-8 shadow-2xl z-10 text-slate-900"
          role="dialog"
          aria-modal="true"
        >
          {/* Header */}
          <div className="flex items-center justify-between pb-5 border-b border-slate-100">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 text-slate-900 shadow-2xs">
                <IconPlug size={20} />
              </div>
              <div>
                <h2 className="text-lg font-bold tracking-tight text-slate-900">
                  Connect Datasets & Sources
                </h2>
                <p className="text-xs font-medium text-slate-500">
                  Choose your data source for deterministic financial reconciliation.
                </p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition-colors"
              aria-label="Close dialog"
            >
              <IconX size={16} />
            </button>
          </div>

          {/* Source Tabs */}
          <div className="grid grid-cols-2 gap-3 pt-6">
            {/* Tab 1: Synthetic Dataset */}
            <button
              type="button"
              onClick={() => setSelectedSource("synthetic")}
              className={`flex flex-col text-left p-4 rounded-2xl border transition-all ${
                selectedSource === "synthetic"
                  ? "border-slate-900 bg-slate-50/80 shadow-xs ring-1 ring-slate-900"
                  : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/50"
              }`}
            >
              <div className="flex items-center justify-between w-full mb-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-900 shadow-2xs">
                  <IconLayers size={16} />
                </div>
                {selectedSource === "synthetic" && (
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-900 text-white">
                    <IconCheck size={12} className="text-white" />
                  </span>
                )}
              </div>
              <h3 className="text-sm font-bold text-slate-900">Generate Synthetic</h3>
              <p className="text-xs text-slate-500 font-medium mt-1 leading-relaxed">
                Deterministic multi-source dataset (50–1,880 records) with payments, refunds, settlements, and edge cases.
              </p>
              <div className="flex flex-wrap gap-1.5 mt-3 pt-3 border-t border-slate-200/60">
                <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[10.5px] font-semibold text-slate-700">
                  Offline-First
                </span>
                <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[10.5px] font-semibold text-slate-700">
                  Anti-Overfitting
                </span>
              </div>
            </button>

            {/* Tab 2: Live Razorpay Test Mode API */}
            <button
              type="button"
              onClick={() => setSelectedSource("razorpay")}
              className={`flex flex-col text-left p-4 rounded-2xl border transition-all ${
                selectedSource === "razorpay"
                  ? "border-slate-900 bg-slate-50/80 shadow-xs ring-1 ring-slate-900"
                  : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/50"
              }`}
            >
              <div className="flex items-center justify-between w-full mb-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-900 shadow-2xs">
                  <IconRazorpay size={18} />
                </div>
                {selectedSource === "razorpay" && (
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-900 text-white">
                    <IconCheck size={12} className="text-white" />
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1.5">
                <h3 className="text-sm font-bold text-slate-900">Razorpay Test Mode</h3>
                {status?.configured ? (
                  <span className="rounded-full bg-emerald-50 border border-emerald-200 px-1.5 py-0.2 text-[10px] font-bold text-emerald-700">
                    Live
                  </span>
                ) : (
                  <span className="rounded-full bg-blue-50 border border-blue-200 px-1.5 py-0.2 text-[10px] font-bold text-blue-700">
                    API
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-500 font-medium mt-1 leading-relaxed">
                Sync live test-mode payments, refunds, and settlements directly from api.razorpay.com.
              </p>
              <div className="flex flex-wrap gap-1.5 mt-3 pt-3 border-t border-slate-200/60">
                <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[10.5px] font-semibold text-slate-700">
                  Read-Only
                </span>
                <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[10.5px] font-semibold text-slate-700">
                  HMAC-SHA256
                </span>
              </div>
            </button>
          </div>

          {/* Action Body Area */}
          <div className="mt-6 pt-5 border-t border-slate-100">
            {selectedSource === "synthetic" ? (
              <div className="space-y-4">
                <div className="flex items-center justify-between p-3.5 rounded-2xl border border-slate-200 bg-slate-50/50">
                  <div className="flex items-center gap-2.5">
                    <IconShield size={16} className="text-slate-900" />
                    <div>
                      <p className="text-xs font-bold text-slate-900">Synthetic Merchant Policy Dataset</p>
                      <p className="text-[11px] text-slate-500 font-medium">100% reproducible with integer paise precision and label firewall.</p>
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => {
                      onRunSynthetic("dev", "rules-only");
                      onClose();
                    }}
                    className="flex items-center justify-center gap-2 rounded-2xl bg-slate-900 px-4 py-3 text-xs font-bold text-white shadow-sm hover:bg-slate-800 transition-colors"
                  >
                    <IconBolt size={14} className="text-white" />
                    Reconcile Dev Batch
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      onRunSynthetic("adversarial", "agent");
                      onClose();
                    }}
                    className="flex items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-xs font-bold text-slate-900 shadow-2xs hover:bg-slate-50 hover:border-slate-300 transition-colors"
                  >
                    <IconSparkles size={14} />
                    Run AI Adversarial Batch
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                {status?.configured ? (
                  <div className="flex items-center justify-between p-3.5 rounded-2xl border border-emerald-200 bg-emerald-50/60">
                    <div className="flex items-center gap-2.5">
                      <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-500 text-white">
                        <IconCheck size={14} className="text-white" />
                      </div>
                      <div>
                        <p className="text-xs font-bold text-emerald-900">
                          Razorpay API Credentials Configured
                        </p>
                        <p className="font-mono text-[11px] text-emerald-700">
                          {status.key_id_masked || "rzp_test_active"} · {status.base_url}
                        </p>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <p className="text-xs font-medium text-slate-600">
                      Enter your Razorpay Test Mode API keys (or save to <code className="font-mono bg-slate-100 px-1 py-0.5 rounded text-[11px]">.env</code>):
                    </p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div>
                        <label className="text-[11px] font-bold text-slate-700 uppercase tracking-wider block mb-1">
                          Key ID
                        </label>
                        <input
                          type="text"
                          value={customKeyId}
                          onChange={(e) => setCustomKeyId(e.target.value)}
                          placeholder="rzp_test_..."
                          className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-mono text-slate-900 placeholder:text-slate-400 focus:outline-none focus:border-slate-400"
                        />
                      </div>
                      <div>
                        <label className="text-[11px] font-bold text-slate-700 uppercase tracking-wider block mb-1">
                          Key Secret
                        </label>
                        <input
                          type="password"
                          value={customKeySecret}
                          onChange={(e) => setCustomKeySecret(e.target.value)}
                          placeholder="••••••••••••••••"
                          className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-mono text-slate-900 placeholder:text-slate-400 focus:outline-none focus:border-slate-400"
                        />
                      </div>
                    </div>
                  </div>
                )}

                {syncError && (
                  <div className="p-3 rounded-xl border border-rose-200 bg-rose-50 text-xs font-semibold text-rose-800">
                    ⚠️ {syncError}
                  </div>
                )}

                {syncResult && (
                  <div className="p-3 rounded-xl border border-emerald-200 bg-emerald-50 text-xs font-semibold text-emerald-900 flex items-center justify-between">
                    <span>
                      ✓ Synced {syncResult.payments_count} payments, {syncResult.refunds_count} refunds, {syncResult.settlements_count} settlements!
                    </span>
                    <span className="text-[11px] font-mono text-emerald-700">Reconciling…</span>
                  </div>
                )}

                <div className="pt-2">
                  <button
                    type="button"
                    onClick={handleRazorpaySync}
                    disabled={syncing}
                    className="w-full flex items-center justify-center gap-2 rounded-2xl bg-slate-900 px-4 py-3 text-xs font-bold text-white shadow-sm hover:bg-slate-800 transition-colors disabled:opacity-50"
                  >
                    <IconRazorpay size={16} className="text-white" />
                    {syncing ? "Connecting to api.razorpay.com…" : "Sync & Reconcile Razorpay Data"}
                  </button>
                </div>
              </div>
            )}
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
}
