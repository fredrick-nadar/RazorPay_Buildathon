/**
 * Control-room UI primitives: panels, badges, chips, skeletons, toasts.
 * Pure presentation; no financial truth logic.
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
      className={`rounded-2xl border border-white/[0.07] bg-[#101013]/90 shadow-[0_1px_0_rgba(255,255,255,0.03)_inset,0_18px_40px_-24px_rgba(0,0,0,0.9)] ${className}`}
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
        className={`flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-[0.14em] ${
          accent ? "text-[#e6b45c]" : "text-zinc-400"
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
      ? "text-emerald-300"
      : tone === "warning"
        ? "text-[#e6b45c]"
        : tone === "critical"
          ? "text-rose-300"
          : tone === "accent"
            ? "text-cyan-300"
            : "text-zinc-100";
  return (
    <div className="min-w-0">
      <div className="text-[9.5px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
        {label}
      </div>
      <div
        className={`mt-1 truncate text-[15px] font-semibold leading-none tracking-tight ${toneClass} ${
          mono ? "font-mono tabular-nums" : ""
        }`}
      >
        {value}
      </div>
      {sub != null && (
        <div className="mt-1 truncate text-[10px] text-zinc-500">{sub}</div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Badge                                                               */
/* ------------------------------------------------------------------ */

const BADGE_TONES = {
  neutral: "border-white/10 bg-white/[0.04] text-zinc-300",
  brass: "border-[#e6b45c]/30 bg-[#e6b45c]/[0.08] text-[#e6b45c]",
  positive: "border-emerald-400/25 bg-emerald-400/[0.08] text-emerald-300",
  warning: "border-amber-400/25 bg-amber-400/[0.08] text-amber-300",
  critical: "border-rose-400/25 bg-rose-400/[0.08] text-rose-300",
  info: "border-cyan-400/25 bg-cyan-400/[0.08] text-cyan-300",
  violet: "border-violet-400/25 bg-violet-400/[0.08] text-violet-300",
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
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-[3px] text-[10px] font-semibold uppercase tracking-[0.08em] ${BADGE_TONES[tone]} ${className}`}
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
      className="group inline-flex items-center gap-1.5 rounded-md border border-white/[0.06] bg-black/40 px-2 py-1 font-mono text-[10.5px] text-zinc-400 transition-colors hover:border-white/20 hover:text-zinc-200 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c]"
    >
      <span className="max-w-[26ch] truncate">{label ?? value}</span>
      {copied ? (
        <IconCheck size={11} className="text-emerald-400" />
      ) : (
        <IconCopy size={11} className="opacity-50 group-hover:opacity-90" />
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
      className={`animate-pulse rounded-lg bg-white/[0.055] ${className}`}
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
      className={`fixed bottom-5 right-5 z-[70] flex max-w-md items-start gap-3 rounded-xl border px-4 py-3 text-xs shadow-2xl backdrop-blur-xl animate-rise ${
        isError
          ? "border-rose-400/30 bg-rose-950/80 text-rose-100"
          : "border-emerald-400/30 bg-emerald-950/80 text-emerald-100"
      }`}
    >
      <span className="mt-[1px] shrink-0">
        {isError ? <IconX size={14} /> : <IconCheck size={14} />}
      </span>
      <p className="leading-relaxed">{toast.message}</p>
      <button
        onClick={onDismiss}
        aria-label="Dismiss notification"
        className="ml-2 shrink-0 opacity-60 transition-opacity hover:opacity-100"
      >
        <IconX size={12} />
      </button>
    </div>
  );
}
