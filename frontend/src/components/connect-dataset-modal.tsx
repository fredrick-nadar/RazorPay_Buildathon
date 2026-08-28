"use client";

import { useEffect, useState, useRef } from "react";
import { AnimatePresence, motion } from "motion/react";
import DecryptedText from "./ui/decrypted-text";
import {
  IconBolt,
  IconCheck,
  IconPlug,
  IconRazorpay,
  IconUpload,
  IconX,
} from "./icons";
import { SandboxExtractionStudio, type ExtractedResult } from "./sandbox-extraction-studio";

interface ConnectDatasetModalProps {
  open: boolean;
  onClose: () => void;
  onSyncSuccess: (runId: string, summary: Record<string, unknown> | null) => void;
}

interface UploadedFileSummary {
  filename: string;
  mapped_filename: string;
  file_type: string;
  rows_count: number;
  checksum_sha256: string;
  status: string;
}

const SYNC_PROGRESS_MESSAGES = [
  "Connecting to Razorpay Test Mode...",
  "Establishing a secure data channel...",
  "Authenticating the reconciliation request...",
  "Fetching payment and settlement records...",
  "Retrieving linked refunds and adjustments...",
  "Normalizing transaction timestamps...",
  "Aligning payment, order and settlement references...",
  "Mapping records across the financial pipeline...",
  "Building the five-way ledger graph...",
  "Tracing each transaction to its settlement path...",
  "Checking for duplicate ledger entries...",
  "Checking for missing ledger postings...",
  "Validating fees, taxes and adjustments...",
  "Comparing expected and settled amounts...",
  "Reconciling refunds against original payments...",
  "Analyzing settlement timing windows...",
  "Resolving cross-source reference mismatches...",
  "Generating integer-paise proofs...",
  "Verifying arithmetic consistency...",
  "Evaluating reconciliation exceptions...",
  "Tracing exceptions back to source records...",
  "Cross-checking evidence across connected records...",
  "Running deterministic financial checks...",
  "Validating every proposed match...",
  "Computing confidence for unresolved records...",
  "Preparing the exception set...",
  "Finalizing verified reconciliation...",
  "Compiling the reconciliation report...",
  "Preparing results for the control room..."
];

const UPLOAD_PROGRESS_MESSAGES = [
  "Reading financial documents...",
  "Parsing columns with Vision...",
  "Canonicalizing vendor headers...",
  "Calculating deterministic matches...",
  "Finalizing verified ledger...",
];

export function ConnectDatasetModal({
  open,
  onClose,
  onSyncSuccess,
}: ConnectDatasetModalProps) {
  const [selectedSource, setSelectedSource] = useState<"razorpay" | "csv">("razorpay");
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

  // CSV Ingest state
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFileSummary[]>([]);
  const [activeSandboxFile, setActiveSandboxFile] = useState<{
    filename: string;
    content?: string;
    contentBase64?: string;
    mimeType?: string;
  } | null>(null);
  const [reconcilingSession, setReconcilingSession] = useState(false);
  const [reconcileMode, setReconcileMode] = useState<"rules-only" | "ai-assisted">("rules-only");
  const [csvError, setCsvError] = useState<string | null>(null);
  const [sessionId] = useState(() => `session_${Math.random().toString(36).slice(2, 9)}`);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Dynamic progress message index
  const [syncMsgIdx, setSyncMsgIdx] = useState(0);
  const [uploadMsgIdx, setUploadMsgIdx] = useState(0);

  useEffect(() => {
    if (!syncing) {
      setSyncMsgIdx(0);
      return;
    }
    const timer = setInterval(() => {
      setSyncMsgIdx((prev) => (prev + 1) % SYNC_PROGRESS_MESSAGES.length);
    }, 2500);
    return () => clearInterval(timer);
  }, [syncing]);

  useEffect(() => {
    if (!reconcilingSession) {
      setUploadMsgIdx(0);
      return;
    }
    const timer = setInterval(() => {
      setUploadMsgIdx((prev) => (prev + 1) % UPLOAD_PROGRESS_MESSAGES.length);
    }, 2500);
    return () => clearInterval(timer);
  }, [reconcilingSession]);

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

  async function handleFilesSelected(files: FileList | null) {
    if (!files || files.length === 0) return;
    const file = files[0];
    if (!file) return;

    setCsvError(null);

    const lowerName = file.name.toLowerCase();
    const isCsv = lowerName.endsWith(".csv");
    const isPdf = lowerName.endsWith(".pdf");
    const isImage =
      lowerName.endsWith(".png") ||
      lowerName.endsWith(".jpg") ||
      lowerName.endsWith(".jpeg") ||
      lowerName.endsWith(".webp");

    if (!isCsv && !isPdf && !isImage) return;

    try {
      if (isCsv) {
        const text = await file.text();
        setActiveSandboxFile({
          filename: file.name,
          content: text,
          mimeType: "text/csv",
        });
      } else {
        const arrayBuffer = await file.arrayBuffer();
        const bytes = new Uint8Array(arrayBuffer);
        let binary = "";
        for (let j = 0; j < bytes.byteLength; j++) {
          binary += String.fromCharCode(bytes[j] ?? 0);
        }
        const base64Content = btoa(binary);
        const mimeType = isPdf
          ? "application/pdf"
          : lowerName.endsWith(".png")
            ? "image/png"
            : lowerName.endsWith(".webp")
              ? "image/webp"
              : "image/jpeg";

        setActiveSandboxFile({
          filename: file.name,
          contentBase64: base64Content,
          mimeType,
        });
      }
    } catch (err) {
      setCsvError(err instanceof Error ? err.message : "Error reading file");
    }
  }

  function handleRemoveFile(filename: string) {
    setUploadedFiles((prev) => prev.filter((f) => f.filename !== filename));
  }

  async function handleReconcileUploadedSession() {
    if (uploadedFiles.length === 0) return;
    setReconcilingSession(true);
    setCsvError(null);

    try {
      const res = await fetch("/api/v1/ingest/reconcile-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          fallback_profile: "dev",
          mode: reconcileMode,
        }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to reconcile uploaded files");
      }

      const data = await res.json();
      if (data.run_id) {
        onSyncSuccess(data.run_id, data.summary);
        onClose();
      }
    } catch (err) {
      setCsvError(err instanceof Error ? err.message : "Error executing reconciliation");
    } finally {
      setReconcilingSession(false);
    }
  }

  if (!open) return null;

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 overflow-y-auto">
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
          className="relative w-full max-w-3xl overflow-hidden rounded-3xl border border-slate-200 bg-white p-6 sm:p-8 shadow-2xl z-10 text-slate-900"
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
                  Connect Datasets & Multi-Source Ingest
                </h2>
                <p className="text-xs font-medium text-slate-500">
                  Choose your data source for deterministic financial flight recording.
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

          {/* Source Tabs (2-way layout: Live API & User Uploads) */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-6">
            {/* Tab 1: Razorpay Live API */}
            <button
              type="button"
              onClick={() => setSelectedSource("razorpay")}
              className={`flex flex-col text-left p-4 rounded-2xl border transition-all ${selectedSource === "razorpay"
                ? "border-slate-900 bg-slate-50/80 shadow-xs ring-1 ring-slate-900"
                : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/50"
                }`}
            >
              <div className="flex items-center justify-between w-full mb-2.5">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-900 shadow-2xs">
                  <IconRazorpay size={18} />
                </div>
                {selectedSource === "razorpay" && (
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-900 text-white">
                    <IconCheck size={12} className="text-white" />
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1.5 flex-wrap">
                <h3 className="text-xs font-bold text-slate-900">Razorpay Live API</h3>
                <span className="rounded-full bg-emerald-50 border border-emerald-200 px-1.5 py-0.5 text-[9px] font-bold text-emerald-700 inline-flex items-center gap-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  Live Gateway
                </span>
              </div>
              <p className="text-[11px] text-slate-500 font-medium mt-1 leading-relaxed">
                Fetch live test-mode orders, payments & settlements directly from Razorpay.
              </p>
            </button>

            {/* Tab 2: Multi-Format Ingestion Zone */}
            <button
              type="button"
              onClick={() => setSelectedSource("csv")}
              className={`flex flex-col text-left p-4 rounded-2xl border transition-all ${selectedSource === "csv"
                ? "border-slate-900 bg-slate-50/80 shadow-xs ring-1 ring-slate-900"
                : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/50"
                }`}
            >
              <div className="flex items-center justify-between w-full mb-2.5">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-900 shadow-2xs">
                  <IconUpload size={16} />
                </div>
                {selectedSource === "csv" && (
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-900 text-white">
                    <IconCheck size={12} className="text-white" />
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1">
                <h3 className="text-xs font-bold text-slate-900">Upload Documents</h3>
                <span className="rounded-full bg-blue-50 border border-blue-200 px-1.5 py-0.2 text-[9px] font-bold text-blue-700">
                  PDF / CSV / OCR
                </span>
              </div>
              <p className="text-[11px] text-slate-500 font-medium mt-1 leading-relaxed">
                Explicitly upload merchant bank statements, settlement sheets, or CSVs.
              </p>
            </button>
          </div>

          {/* Action Body Area */}
          <div className="mt-6 pt-5 border-t border-slate-100">

            {selectedSource === "razorpay" && (
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
                      Enter your Razorpay Test Mode API keys:
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
                    className="w-full flex items-center justify-center gap-2 rounded-2xl bg-slate-900 px-4 py-3 text-xs font-bold text-white shadow-sm hover:bg-slate-800 transition-all disabled:opacity-80"
                  >
                    <IconRazorpay size={16} className={`text-white shrink-0 ${syncing ? "animate-pulse" : ""}`} />
                    <span className="transition-all duration-300">
                      {syncing ? (
                        <DecryptedText
                          text={SYNC_PROGRESS_MESSAGES[syncMsgIdx] ?? "Syncing live data..."}
                          speed={35}
                          maxIterations={12}
                          sequential={true}
                          revealDirection="start"
                          animateOn="view"
                          className="text-white font-mono"
                          encryptedClassName="text-slate-400 font-mono"
                        />
                      ) : (
                        "Sync & Reconcile Live Gateway Data"
                      )}
                    </span>
                  </button>
                </div>
              </div>
            )}

            {selectedSource === "csv" && (
              activeSandboxFile ? (
                <SandboxExtractionStudio
                  filename={activeSandboxFile.filename}
                  content={activeSandboxFile.content}
                  contentBase64={activeSandboxFile.contentBase64}
                  mimeType={activeSandboxFile.mimeType}
                  sessionId={sessionId}
                  onCommit={(res: ExtractedResult) => {
                    setUploadedFiles((prev) => [
                      ...prev.filter(
                        (f) => f.filename !== res.filename && f.mapped_filename !== res.mapped_filename
                      ),
                      {
                        filename: res.filename,
                        mapped_filename: res.mapped_filename,
                        file_type: res.file_type,
                        rows_count: res.rows_count,
                        checksum_sha256: res.checksum_sha256,
                        status: res.status,
                      },
                    ]);
                    setActiveSandboxFile(null);
                  }}
                  onCancel={() => setActiveSandboxFile(null)}
                />
              ) : (
                <div className="space-y-4">
                  {/* Drag-and-drop drop zone */}
                  <input
                    type="file"
                    ref={fileInputRef}
                    onChange={(e) => void handleFilesSelected(e.target.files)}
                    accept=".csv,.pdf,.png,.jpg,.jpeg,.webp"
                    multiple
                    className="hidden"
                  />

                  <div
                    onClick={() => fileInputRef.current?.click()}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => {
                      e.preventDefault();
                      void handleFilesSelected(e.dataTransfer.files);
                    }}
                    className="border-2 border-dashed border-slate-300 hover:border-slate-900 rounded-3xl p-6 text-center cursor-pointer transition-all bg-slate-50/50 hover:bg-slate-50 flex flex-col items-center justify-center gap-2"
                  >
                    <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-white border border-slate-200 shadow-2xs text-slate-800">
                      <IconUpload size={20} />
                    </div>
                    <div>
                      <p className="text-xs font-bold text-slate-900">
                        Click or drag CSV, PDF statements, or screenshot images to launch Sandbox Studio
                      </p>
                      <p className="text-[11px] text-slate-500 mt-0.5">
                        Supports CSV, PDF bank statements, and payment settlement screenshots (PNG/JPG/WEBP)
                      </p>
                    </div>
                  </div>

                  {csvError && (
                    <div className="p-3 rounded-xl border border-rose-200 bg-rose-50 text-xs font-semibold text-rose-800">
                      ⚠️ {csvError}
                    </div>
                  )}

                  {/* Uploaded files summary list */}
                  {uploadedFiles.length > 0 && (
                    <div className="space-y-2">
                      <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
                        Validated Ingestion Files ({uploadedFiles.length})
                      </div>
                      <div className="space-y-2 max-h-48 overflow-y-auto">
                        {uploadedFiles.map((file) => {
                          const isPdf = file.filename.toLowerCase().endsWith(".pdf");
                          const isCsv = file.filename.toLowerCase().endsWith(".csv");
                          const extLabel = isPdf ? "PDF" : isCsv ? "CSV" : "IMG";
                          const extColor = isPdf
                            ? "bg-rose-50 text-rose-700 border-rose-200"
                            : isCsv
                              ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                              : "bg-blue-50 text-blue-700 border-blue-200";

                          return (
                            <div
                              key={`${file.filename}-${file.mapped_filename}`}
                              className="flex items-center justify-between p-2.5 rounded-xl border border-slate-200 bg-white text-xs shadow-2xs hover:border-slate-300 transition-all"
                            >
                              <div className="flex items-center gap-2 min-w-0 pr-2">
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[9px] font-mono font-bold border shrink-0 ${extColor}`}
                                >
                                  {extLabel}
                                </span>
                                <div className="min-w-0">
                                  <p className="font-semibold text-slate-900 truncate max-w-[200px] sm:max-w-[260px]">
                                    {file.filename}
                                  </p>
                                  <p className="font-mono text-[10px] text-slate-500 flex items-center gap-1">
                                    <span>↳ mapped as:</span>
                                    <span className="font-bold text-slate-700">{file.mapped_filename}</span>
                                    <span>•</span>
                                    <span className="font-semibold text-slate-600">{file.rows_count} {file.rows_count === 1 ? "row" : "rows"}</span>
                                  </p>
                                </div>
                              </div>
                              <div className="flex items-center gap-2 shrink-0">
                                <span className="rounded-full bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-[10px] font-bold text-emerald-700 inline-flex items-center gap-1">
                                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                                  Validated
                                </span>
                                <button
                                  type="button"
                                  onClick={() => handleRemoveFile(file.filename)}
                                  className="h-6 w-6 flex items-center justify-center rounded-lg text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition-colors"
                                  title="Remove file"
                                >
                                  <IconX size={12} />
                                </button>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* 3-Pillar Ingestion Triad Checklist */}
                  {(() => {
                    const hasRazorpay = uploadedFiles.some(
                      (f) =>
                        ["payments", "settlements"].includes(f.file_type.toLowerCase()) ||
                        ["payments.csv", "settlements.csv"].includes(f.mapped_filename.toLowerCase())
                    );
                    const razorpayFile = uploadedFiles.find(
                      (f) =>
                        ["payments", "settlements"].includes(f.file_type.toLowerCase()) ||
                        ["payments.csv", "settlements.csv"].includes(f.mapped_filename.toLowerCase())
                    );

                    const hasBank = uploadedFiles.some(
                      (f) =>
                        ["bank_entries", "bank", "bank_statements"].includes(f.file_type.toLowerCase()) ||
                        f.mapped_filename.toLowerCase() === "bank_entries.csv"
                    );
                    const bankFile = uploadedFiles.find(
                      (f) =>
                        ["bank_entries", "bank", "bank_statements"].includes(f.file_type.toLowerCase()) ||
                        f.mapped_filename.toLowerCase() === "bank_entries.csv"
                    );

                    const hasLedger = uploadedFiles.some(
                      (f) =>
                        ["ledger_entries", "ledger"].includes(f.file_type.toLowerCase()) ||
                        f.mapped_filename.toLowerCase() === "ledger_entries.csv"
                    );
                    const ledgerFile = uploadedFiles.find(
                      (f) =>
                        ["ledger_entries", "ledger"].includes(f.file_type.toLowerCase()) ||
                        f.mapped_filename.toLowerCase() === "ledger_entries.csv"
                    );

                    const allThreePresent = hasRazorpay && hasBank && hasLedger;
                    const presentCount = (hasRazorpay ? 1 : 0) + (hasBank ? 1 : 0) + (hasLedger ? 1 : 0);

                    return (
                      <div className="space-y-3">
                        <div className="flex items-center justify-between">
                          <div>
                            <h4 className="text-[11px] font-bold text-slate-900 uppercase tracking-wider">
                              Reconciliation Triad Checklist
                            </h4>
                            <p className="text-[10px] text-slate-500">
                              3 pillars required for complete 100% automated invariant reconciliation
                            </p>
                          </div>
                          <span
                            className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-full border ${
                              allThreePresent
                                ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                                : "bg-amber-50 text-amber-700 border-amber-200"
                            }`}
                          >
                            {presentCount}/3 Pillars Verified
                          </span>
                        </div>

                        {/* 3 Pillars Grid */}
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
                          {/* Pillar 1: Razorpay */}
                          <div
                            className={`p-3 rounded-2xl border transition-all ${
                              hasRazorpay
                                ? "border-emerald-300 bg-emerald-50/60 shadow-2xs"
                                : "border-slate-200 bg-slate-50/50"
                            }`}
                          >
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-[9px] font-bold uppercase tracking-wider text-slate-500">
                                Pillar 1: Gateway
                              </span>
                              <span
                                className={`flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold ${
                                  hasRazorpay
                                    ? "bg-emerald-500 text-white"
                                    : "border border-slate-300 text-slate-400 bg-white"
                                }`}
                              >
                                {hasRazorpay ? "✓" : "1"}
                              </span>
                            </div>
                            <p className="text-xs font-bold text-slate-900">
                              Razorpay Payments / Payouts
                            </p>
                            <p className="text-[10px] font-mono text-slate-500 mt-0.5 truncate">
                              {hasRazorpay
                                ? `${razorpayFile?.filename} (${razorpayFile?.rows_count} rows)`
                                : "Pending: Upload CSV/PDF"}
                            </p>
                          </div>

                          {/* Pillar 2: Bank Statement */}
                          <div
                            className={`p-3 rounded-2xl border transition-all ${
                              hasBank
                                ? "border-emerald-300 bg-emerald-50/60 shadow-2xs"
                                : "border-slate-200 bg-slate-50/50"
                            }`}
                          >
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-[9px] font-bold uppercase tracking-wider text-slate-500">
                                Pillar 2: Bank Feed
                              </span>
                              <span
                                className={`flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold ${
                                  hasBank
                                    ? "bg-emerald-500 text-white"
                                    : "border border-slate-300 text-slate-400 bg-white"
                                }`}
                              >
                                {hasBank ? "✓" : "2"}
                              </span>
                            </div>
                            <p className="text-xs font-bold text-slate-900">
                              Bank Statement / UTRs
                            </p>
                            <p className="text-[10px] font-mono text-slate-500 mt-0.5 truncate">
                              {hasBank
                                ? `${bankFile?.filename} (${bankFile?.rows_count} rows)`
                                : "Pending: Upload HDFC/ICICI PDF"}
                            </p>
                          </div>

                          {/* Pillar 3: Merchant Ledger */}
                          <div
                            className={`p-3 rounded-2xl border transition-all ${
                              hasLedger
                                ? "border-emerald-300 bg-emerald-50/60 shadow-2xs"
                                : "border-slate-200 bg-slate-50/50"
                            }`}
                          >
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-[9px] font-bold uppercase tracking-wider text-slate-500">
                                Pillar 3: General Ledger
                              </span>
                              <span
                                className={`flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold ${
                                  hasLedger
                                    ? "bg-emerald-500 text-white"
                                    : "border border-slate-300 text-slate-400 bg-white"
                                }`}
                              >
                                {hasLedger ? "✓" : "3"}
                              </span>
                            </div>
                            <p className="text-xs font-bold text-slate-900">
                              ERP Merchant Ledger
                            </p>
                            <p className="text-[10px] font-mono text-slate-500 mt-0.5 truncate">
                              {hasLedger
                                ? `${ledgerFile?.filename} (${ledgerFile?.rows_count} rows)`
                                : "Pending: Upload Tally/Zoho CSV"}
                            </p>
                          </div>
                        </div>

                        {/* Triad Status Notification */}
                        {uploadedFiles.length > 0 && (
                          <div
                            className={`p-3 rounded-2xl border text-xs ${
                              allThreePresent
                                ? "border-emerald-200 bg-emerald-50/80 text-emerald-900"
                                : "border-amber-200 bg-amber-50/80 text-amber-900"
                            }`}
                          >
                            {allThreePresent ? (
                              <div className="flex items-center gap-2 font-semibold">
                                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-600 text-white shrink-0 text-[10px]">
                                  ✓
                                </span>
                                <span>
                                  3-Way Triad Complete: Automated 100% invariant reconciliation is ready to run.
                                </span>
                              </div>
                            ) : (
                              <div className="space-y-0.5">
                                <div className="flex items-center gap-2 font-bold text-amber-950">
                                  <span>⚠️ Partial Triad Mode ({presentCount} of 3 Pillars Attached)</span>
                                </div>
                                <p className="text-[11px] text-amber-800 leading-relaxed">
                                  Missing sources will automatically be flagged as unlinked exceptions for manual audit review.
                                </p>
                              </div>
                            )}
                          </div>
                        )}

                        {/* Execution Mode Selector */}
                        <div className="pt-2">
                          <label className="text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-1.5 block">
                            Reconciliation Engine Mode
                          </label>
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                            <button
                              type="button"
                              onClick={() => setReconcileMode("rules-only")}
                              className={`p-2.5 rounded-2xl border text-left transition-all ${
                                reconcileMode === "rules-only"
                                  ? "border-slate-900 bg-slate-900 text-white shadow-xs"
                                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50/50"
                              }`}
                            >
                              <div className="flex items-center justify-between">
                                <span className="text-xs font-bold">⚡ Rules-Only Engine</span>
                                {reconcileMode === "rules-only" && (
                                  <span className="text-[9px] bg-white/20 px-1.5 py-0.5 rounded-full font-mono">
                                    Active
                                  </span>
                                )}
                              </div>
                              <p
                                className={`text-[10px] mt-0.5 leading-relaxed ${
                                  reconcileMode === "rules-only" ? "text-slate-300" : "text-slate-500"
                                }`}
                              >
                                100% Deterministic Integer Math (Instant verification)
                              </p>
                            </button>

                            <button
                              type="button"
                              onClick={() => setReconcileMode("ai-assisted")}
                              className={`p-2.5 rounded-2xl border text-left transition-all ${
                                reconcileMode === "ai-assisted"
                                  ? "border-indigo-600 bg-indigo-600 text-white shadow-xs"
                                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50/50"
                              }`}
                            >
                              <div className="flex items-center justify-between">
                                <span className="text-xs font-bold">🤖 AI-Assisted Agent</span>
                                {reconcileMode === "ai-assisted" && (
                                  <span className="text-[9px] bg-white/20 px-1.5 py-0.5 rounded-full font-mono">
                                    Groq / Gemini
                                  </span>
                                )}
                              </div>
                              <p
                                className={`text-[10px] mt-0.5 leading-relaxed ${
                                  reconcileMode === "ai-assisted" ? "text-indigo-200" : "text-slate-500"
                                }`}
                              >
                                Rules Match + LLM Root Cause Investigation Dossier
                              </p>
                            </button>
                          </div>
                        </div>

                        <div className="pt-1">
                          <button
                            type="button"
                            onClick={handleReconcileUploadedSession}
                            disabled={uploadedFiles.length === 0 || reconcilingSession}
                            className="w-full flex items-center justify-center gap-2 rounded-2xl bg-slate-900 px-4 py-3 text-xs font-bold text-white shadow-sm hover:bg-slate-800 transition-all disabled:opacity-80"
                          >
                            <IconBolt
                              size={14}
                              className={`text-white shrink-0 ${reconcilingSession ? "animate-pulse" : ""}`}
                            />
                            <span className="transition-all duration-300">
                              {reconcilingSession ? (
                                <DecryptedText
                                  text={UPLOAD_PROGRESS_MESSAGES[uploadMsgIdx] ?? "Reconciling uploaded files..."}
                                  speed={35}
                                  maxIterations={12}
                                  sequential={true}
                                  revealDirection="start"
                                  animateOn="view"
                                  className="text-white font-mono"
                                  encryptedClassName="text-slate-400 font-mono"
                                />
                              ) : allThreePresent ? (
                                "Run Full Automated 3-Way Reconciliation (All 3 Pillars Verified)"
                              ) : (
                                `Reconcile Dataset (${uploadedFiles.length} files attached · Missing will be flagged)`
                              )}
                            </span>
                          </button>
                        </div>
                      </div>
                    );
                  })()}
                </div>
              )
            )}
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
}
