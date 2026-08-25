/**
 * Control-room UI primitives: panels, badges, chips, skeletons, toasts.
 * Pure presentation; clean, minimal, bright & professional.
 */

"use client";

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { IconCheck, IconCopy, IconX } from "./icons";

/* ------------------------------------------------------------------ */
/* Panel                                                               */
/* ------------------------------------------------------------------ */

export function Panel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-xl border border-slate-200 bg-white shadow-sm transition-all ${className}`}
    >
      {children}
    </section>
  );
}

export function SectionLabel({
  children,
  accent = false,
  right,
}: {
  children: ReactNode;
  accent?: boolean;
  right?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <h2
        className={`flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider ${
          accent ? "text-blue-600" : "text-slate-700"
        }`}
      >
        {children}
      </h2>
      {right}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Metric                                                              */
/* ------------------------------------------------------------------ */

export function Metric({
  label,
  value,
  sub,
  tone = "default",
  mono = true,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "default" | "positive" | "warning" | "critical" | "accent";
  mono?: boolean;
}) {
  const toneClass =
    tone === "positive"
      ? "text-emerald-600"
      : tone === "warning"
        ? "text-amber-600"
        : tone === "critical"
          ? "text-rose-600"
          : tone === "accent"
            ? "text-blue-600"
            : "text-slate-900";
  return (
    <div className="min-w-0 rounded-lg border border-slate-100 bg-white p-3 shadow-sm hover:border-slate-200 transition-all">
      <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
        {label}
      </div>
      <div
        className={`mt-1.5 truncate text-[16px] font-bold leading-none tracking-tight ${toneClass} ${
          mono ? "font-mono tabular-nums" : ""
        }`}
      >
        {value}
      </div>
      {sub != null && (
        <div className="mt-1 truncate text-[10.5px] font-medium text-slate-500">{sub}</div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Badge                                                               */
/* ------------------------------------------------------------------ */

const BADGE_TONES = {
  neutral: "border-slate-200 bg-slate-100/90 text-slate-700",
  brass: "border-amber-200 bg-amber-50 text-amber-800",
  positive: "border-emerald-200 bg-emerald-50 text-emerald-800",
  warning: "border-amber-200 bg-amber-50 text-amber-800",
  critical: "border-rose-200 bg-rose-50 text-rose-800",
  info: "border-blue-200 bg-blue-50 text-blue-800",
  violet: "border-purple-200 bg-purple-50 text-purple-800",
} as const;

export type BadgeTone = keyof typeof BADGE_TONES;

export function Badge({
  children,
  tone = "neutral",
  icon,
  className = "",
}: {
  children: ReactNode;
  tone?: BadgeTone;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-0.5 text-[10.5px] font-semibold tracking-wide ${BADGE_TONES[tone]} ${className}`}
    >
      {icon}
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Copy chip                                                           */
/* ------------------------------------------------------------------ */

export function CopyChip({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1800);
    return () => clearTimeout(t);
  }, [copied]);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard.writeText(value);
        setCopied(true);
      }}
      title={label ?? `Copy ${value}`}
      aria-label={label ?? `Copy ${value}`}
      className="group inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-[10.5px] text-slate-600 transition-colors hover:border-slate-300 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
    >
      <span className="max-w-[26ch] truncate">{label ?? value}</span>
      {copied ? (
        <IconCheck size={11} className="text-emerald-600" />
      ) : (
        <IconCopy size={11} className="text-slate-400 group-hover:text-slate-600" />
      )}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Skeleton                                                            */
/* ------------------------------------------------------------------ */

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded-lg bg-slate-200/80 ${className}`}
    />
  );
}

/* ------------------------------------------------------------------ */
/* Toast                                                               */
/* ------------------------------------------------------------------ */

export interface ToastState {
  kind: "error" | "success";
  message: string;
}

export function Toast({
  toast,
  onDismiss,
}: {
  toast: ToastState | null;
  onDismiss: () => void;
}) {
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(onDismiss, 5000);
    return () => clearTimeout(t);
  }, [toast, onDismiss]);
  if (!toast) return null;
  const isError = toast.kind === "error";
  return (
    <div
      role="status"
      className={`fixed bottom-5 right-5 z-[70] flex max-w-md items-start gap-3 rounded-xl border bg-white p-4 text-xs shadow-xl animate-rise ${
        isError
          ? "border-rose-200 text-rose-900"
          : "border-emerald-200 text-emerald-900"
      }`}
    >
      <span className={`mt-0.5 shrink-0 ${isError ? "text-rose-600" : "text-emerald-600"}`}>
        {isError ? <IconX size={15} /> : <IconCheck size={15} />}
      </span>
      <p className="font-medium leading-relaxed">{toast.message}</p>
      <button
        onClick={onDismiss}
        aria-label="Dismiss notification"
        className="ml-2 shrink-0 text-slate-400 transition-colors hover:text-slate-700"
      >
        <IconX size={13} />
      </button>
    </div>
  );
}
