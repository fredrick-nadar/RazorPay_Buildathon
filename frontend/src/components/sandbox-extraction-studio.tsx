"use client";

import { useEffect, useRef, useState } from "react";
import {
  IconCheck,
  IconCopy,
  IconX,
} from "./icons";

function IconTerminal({ size = 16, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <polyline points="4 17 10 11 4 5" />
      <line x1="12" y1="19" x2="20" y2="19" />
    </svg>
  );
}

function IconFileText({ size = 16, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
      <line x1="10" y1="9" x2="8" y2="9" />
    </svg>
  );
}

export interface SandboxTask {
  id: number;
  name: string;
  status: "pending" | "running" | "done" | "error";
  description?: string;
}

export interface ExtractedResult {
  filename: string;
  mapped_filename: string;
  file_type: string;
  rows_count: number;
  checksum_sha256: string;
  preview_rows: Record<string, unknown>[];
  canonical_csv: string;
  session_id: string;
  status: string;
}

interface SandboxExtractionStudioProps {
  filename: string;
  content?: string;
  contentBase64?: string;
  mimeType?: string;
  sessionId: string;
  onCommit: (result: ExtractedResult) => void;
  onCancel: () => void;
}

export function SandboxExtractionStudio({
  filename,
  content = "",
  contentBase64 = "",
  mimeType = "text/csv",
  sessionId,
  onCommit,
  onCancel,
}: SandboxExtractionStudioProps) {
  const [tasks, setTasks] = useState<SandboxTask[]>([
    {
      id: 1,
      name: "Read & inspect document structure",
      status: "running",
      description: `Analyzing format and binary headers for ${filename}`,
    },
    {
      id: 2,
      name: "Execute Python table extractor",
      status: "pending",
      description: "Parsing tabular transaction vectors and cell geometries",
    },
    {
      id: 3,
      name: "Normalize schema & column aliases",
      status: "pending",
      description: "Mapping headers to AdapterSpec invariants (gross, fee, tax, utr)",
    },
    {
      id: 4,
      name: "Verify arithmetic & integer-paise invariants",
      status: "pending",
      description: "Validating decimal precision and generating SHA-256 hash",
    },
  ]);

  const [codeSnippet, setCodeSnippet] = useState<string>("");
  const [terminalLogs, setTerminalLogs] = useState<string[]>([]);
  const [isExecuting, setIsExecuting] = useState<boolean>(true);
  const [extractionResult, setExtractionResult] = useState<ExtractedResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"terminal" | "code" | "preview">("terminal");
  const [copiedCode, setCopiedCode] = useState<boolean>(false);

  const terminalEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll terminal on new log lines
  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [terminalLogs]);

  // Execute extraction via Server-Sent Events (SSE) stream
  useEffect(() => {
    let isMounted = true;
    const abortController = new AbortController();

    async function startStream() {
      try {
        const response = await fetch("/api/v1/ingest/stream-extract", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            filename,
            content,
            content_base64: contentBase64,
            mime_type: mimeType,
            session_id: sessionId,
          }),
          signal: abortController.signal,
        });

        if (!response.ok) {
          const errData = await response.json().catch(() => ({ detail: "Extraction failed" }));
          throw new Error(errData.detail || "Failed to start extraction stream");
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error("Stream response body is empty");

        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split("\n\n");
          buffer = parts.pop() || "";

          for (const part of parts) {
            const trimmed = part.trim();
            if (!trimmed.startsWith("data: ")) continue;

            try {
              const eventData = JSON.parse(trimmed.replace("data: ", ""));
              if (!isMounted) return;

              switch (eventData.type) {
                case "task_init":
                  if (eventData.tasks) setTasks(eventData.tasks);
                  break;

                case "task_update":
                  setTasks((prev) =>
                    prev.map((t) =>
                      t.id === eventData.task_id
                        ? { ...t, status: eventData.status }
                        : t
                    )
                  );
                  break;

                case "stdout":
                  if (eventData.line) {
                    setTerminalLogs((prev) => [...prev, eventData.line]);
                  }
                  break;

                case "code_ready":
                  if (eventData.code) {
                    setCodeSnippet(eventData.code);
                  }
                  break;

                case "error":
                  setErrorMsg(eventData.detail || "An error occurred during extraction.");
                  setIsExecuting(false);
                  break;

                case "complete":
                  if (eventData.result) {
                    setExtractionResult(eventData.result);
                    setActiveTab("preview");
                  }
                  setIsExecuting(false);
                  break;
              }
            } catch {
              // Ignore partial JSON parse errors
            }
          }
        }
      } catch (err: unknown) {
        if (!isMounted) return;
        if (err instanceof Error && err.name === "AbortError") return;
        setErrorMsg(err instanceof Error ? err.message : "Error connecting to sandbox runner");
        setIsExecuting(false);
      }
    }

    void startStream();

    return () => {
      isMounted = false;
      abortController.abort();
    };
  }, [filename, content, contentBase64, mimeType, sessionId]);

  const doneCount = tasks.filter((t) => t.status === "done").length;
  const activeTask = tasks.find((t) => t.status === "running") || tasks.find((t) => t.status === "pending");

  function handleCopyCode() {
    if (!codeSnippet) return;
    void navigator.clipboard.writeText(codeSnippet);
    setCopiedCode(true);
    setTimeout(() => setCopiedCode(false), 2000);
  }

  function handleCommitResult() {
    if (!extractionResult) return;
    // Commit via API
    void fetch("/api/v1/ingest/commit-extracted", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        target_filename: extractionResult.mapped_filename,
        canonical_csv: extractionResult.canonical_csv,
      }),
    });
    onCommit(extractionResult);
  }

  return (
    <div className="flex flex-col space-y-4 text-slate-900">
      {/* Header Banner */}
      <div className="flex items-center justify-between p-3.5 rounded-2xl border border-slate-200 bg-slate-900 text-white shadow-md">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-slate-800 border border-slate-700 text-emerald-400">
            <IconTerminal size={18} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-xs font-bold tracking-tight text-white">
                Argus Sandbox Extraction Studio
              </h3>
              {isExecuting ? (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 text-[9px] font-mono font-bold">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  LIVE EXECUTION
                </span>
              ) : (
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-blue-500/20 text-blue-400 border border-blue-500/30 text-[9px] font-mono font-bold">
                  ✓ VERIFIED
                </span>
              )}
            </div>
            <p className="text-[11px] text-slate-300 font-mono mt-0.5">
              File: <span className="text-white font-semibold">{filename}</span> · Session: {sessionId}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="h-7 w-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
          title="Cancel execution"
        >
          <IconX size={14} />
        </button>
      </div>

      {/* Progress & Task List */}
      <div className="p-4 rounded-2xl border border-slate-200 bg-slate-50/70 space-y-3">
        <div className="flex items-center justify-between text-xs font-semibold text-slate-700">
          <span className="flex items-center gap-1.5">
            <span>To-Do Checklist:</span>
            <span className="text-slate-900 font-bold">
              Done {doneCount} of {tasks.length}
            </span>
          </span>
          {activeTask && isExecuting && (
            <span className="text-[11px] font-mono text-emerald-600 truncate max-w-[280px]">
              Active: #{activeTask.id} {activeTask.name}
            </span>
          )}
        </div>

        {/* Task rows */}
        <div className="space-y-1.5">
          {tasks.map((task) => {
            const isDone = task.status === "done";
            const isRunning = task.status === "running";
            const isError = task.status === "error";

            return (
              <div
                key={task.id}
                className={`flex items-center justify-between p-2.5 rounded-xl border text-xs transition-all ${
                  isRunning
                    ? "border-emerald-500 bg-emerald-50/70 shadow-2xs"
                    : isDone
                    ? "border-slate-200 bg-white"
                    : isError
                    ? "border-rose-300 bg-rose-50"
                    : "border-slate-200/60 bg-white/60 opacity-60"
                }`}
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  <span
                    className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
                      isDone
                        ? "bg-emerald-500 text-white"
                        : isRunning
                        ? "bg-emerald-100 text-emerald-700 ring-2 ring-emerald-500 animate-pulse"
                        : isError
                        ? "bg-rose-500 text-white"
                        : "bg-slate-200 text-slate-600"
                    }`}
                  >
                    {isDone ? "✓" : isError ? "!" : task.id}
                  </span>
                  <div className="min-w-0">
                    <p className={`font-semibold truncate ${isDone ? "text-slate-700" : isRunning ? "text-emerald-950 font-bold" : "text-slate-800"}`}>
                      {task.name}
                    </p>
                    {task.description && (
                      <p className="text-[10px] text-slate-500 truncate">{task.description}</p>
                    )}
                  </div>
                </div>

                <div className="shrink-0 pl-2">
                  {isRunning && (
                    <span className="text-[10px] font-mono font-bold text-emerald-700 inline-flex items-center gap-1">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-ping" />
                      Running...
                    </span>
                  )}
                  {isDone && (
                    <span className="text-[10px] font-mono font-semibold text-slate-500">
                      Done
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Main Sandbox Interactive Tabs */}
      <div className="rounded-2xl border border-slate-200 overflow-hidden bg-slate-950 shadow-inner">
        {/* Tab Controls */}
        <div className="flex items-center justify-between border-b border-slate-800 bg-slate-900/90 px-3 py-2">
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setActiveTab("terminal")}
              className={`flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-mono font-medium transition-all ${
                activeTab === "terminal"
                  ? "bg-slate-800 text-emerald-400 border border-slate-700"
                  : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
              }`}
            >
              <IconTerminal size={12} />
              Live STDOUT ({terminalLogs.length})
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("code")}
              className={`flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-mono font-medium transition-all ${
                activeTab === "code"
                  ? "bg-slate-800 text-emerald-400 border border-slate-700"
                  : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
              }`}
            >
              <IconFileText size={12} />
              Python Ingestion Script
            </button>
            {extractionResult && (
              <button
                type="button"
                onClick={() => setActiveTab("preview")}
                className={`flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-mono font-medium transition-all ${
                  activeTab === "preview"
                    ? "bg-emerald-950/80 text-emerald-300 border border-emerald-800"
                    : "text-emerald-400/70 hover:text-emerald-300 hover:bg-slate-800/50"
                }`}
              >
                <IconCheck size={12} />
                Extracted Data Preview ({extractionResult.rows_count})
              </button>
            )}
          </div>

          {activeTab === "code" && codeSnippet && (
            <button
              type="button"
              onClick={handleCopyCode}
              className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono text-slate-400 hover:text-white bg-slate-800 hover:bg-slate-700 border border-slate-700 transition-colors"
            >
              <IconCopy size={11} />
              {copiedCode ? "Copied!" : "Copy Code"}
            </button>
          )}
        </div>

        {/* Tab 1: Terminal STDOUT */}
        {activeTab === "terminal" && (
          <div className="p-4 font-mono text-xs text-slate-300 h-56 overflow-y-auto space-y-1 bg-slate-950">
            {terminalLogs.length === 0 ? (
              <div className="text-slate-600 italic">Initializing Python sandbox execution environment...</div>
            ) : (
              terminalLogs.map((line, idx) => {
                const isInfo = line.includes("[INFO]");
                const isExec = line.includes("[EXEC]");
                const isNorm = line.includes("[NORM]");
                const isVerify = line.includes("[VERIFY]");
                const isSuccess = line.includes("[SUCCESS]");

                return (
                  <div key={idx} className="flex items-start gap-2 leading-relaxed">
                    <span className="text-slate-600 select-none text-[10px] w-5 text-right">{idx + 1}</span>
                    <span
                      className={`${
                        isSuccess
                          ? "text-emerald-400 font-bold"
                          : isVerify
                          ? "text-cyan-400"
                          : isNorm
                          ? "text-amber-400"
                          : isExec
                          ? "text-purple-400"
                          : isInfo
                          ? "text-slate-300"
                          : "text-slate-400"
                      }`}
                    >
                      {line}
                    </span>
                  </div>
                );
              })
            )}
            {isExecuting && (
              <div className="flex items-center gap-1 text-emerald-400 pt-1">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                <span className="animate-pulse">_</span>
              </div>
            )}
            <div ref={terminalEndRef} />
          </div>
        )}

        {/* Tab 2: Python Code View */}
        {activeTab === "code" && (
          <div className="p-4 font-mono text-xs text-emerald-300 h-56 overflow-y-auto bg-slate-950 whitespace-pre leading-relaxed">
            {codeSnippet || "# Python execution snippet will appear once task completes..."}
          </div>
        )}

        {/* Tab 3: Extracted Preview Table */}
        {activeTab === "preview" && extractionResult && (
          <div className="p-4 bg-white h-56 overflow-y-auto space-y-3">
            <div className="flex items-center justify-between text-xs pb-2 border-b border-slate-100">
              <div className="flex items-center gap-2">
                <span className="font-bold text-slate-900">
                  Target Schema: {extractionResult.mapped_filename}
                </span>
                <span className="rounded-full bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-[10px] font-bold text-emerald-700">
                  {extractionResult.rows_count} {extractionResult.rows_count === 1 ? "Record" : "Records"}
                </span>
              </div>
              <span className="text-[10px] font-mono text-slate-500">
                SHA: {extractionResult.checksum_sha256.slice(0, 16)}...
              </span>
            </div>

            {/* Table */}
            <div className="border border-slate-200 rounded-xl overflow-hidden shadow-2xs">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 text-slate-700 font-semibold border-b border-slate-200">
                  <tr>
                    {Object.keys(extractionResult.preview_rows[0] || {}).slice(0, 5).map((col) => (
                      <th key={col} className="px-3 py-2 font-mono text-[10px] uppercase">
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 font-mono text-[11px] text-slate-800">
                  {extractionResult.preview_rows.slice(0, 5).map((row, idx) => (
                    <tr key={idx} className="hover:bg-slate-50/80">
                      {Object.values(row).slice(0, 5).map((val, colIdx) => (
                        <td key={colIdx} className="px-3 py-1.5 truncate max-w-[140px]">
                          {String(val ?? "-")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* Error Banner */}
      {errorMsg && (
        <div className="p-3 rounded-xl border border-rose-200 bg-rose-50 text-xs font-semibold text-rose-800">
          ⚠️ {errorMsg}
        </div>
      )}

      {/* Action Footer */}
      <div className="flex items-center justify-between pt-2">
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2.5 rounded-xl border border-slate-200 text-xs font-semibold text-slate-700 hover:bg-slate-100 transition-colors"
        >
          Cancel
        </button>

        {extractionResult && (
          <button
            type="button"
            onClick={handleCommitResult}
            className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-slate-900 text-xs font-bold text-white shadow-sm hover:bg-slate-800 transition-all"
          >
            <IconCheck size={14} className="text-white" />
            Commit & Ingest Verified File ({extractionResult.mapped_filename})
          </button>
        )}
      </div>
    </div>
  );
}
