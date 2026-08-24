/**
 * Human authority modal. Approval and rejection are explicit demo-user
 * actions: reviewer identity is editable, notes are never auto-filled with
 * fabricated justification text.
 */

"use client";

import { useEffect, useRef, useState } from "react";
import type { CaseDetail } from "../lib/types";
import { formatINR } from "../lib/format";
import { IconShield, IconX } from "./icons";

export function ApprovalModal({
  detail,
  action,
  busy,
  onClose,
  onConfirm,
}: {
  detail: CaseDetail;
  action: "APPROVE" | "REJECT";
  busy: boolean;
  onClose: () => void;
  onConfirm: (reviewerId: string, notes: string) => void;
}) {
  const [reviewerId, setReviewerId] = useState("reviewer-finance-ops");
  const [notes, setNotes] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  const approving = action === "APPROVE";

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const deltaText =
    detail.case.proposed_delta_paise !== null
      ? formatINR(detail.case.proposed_delta_paise)
      : "\u2014";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={approving ? "Authorize simulated correction" : "Reject proposed correction"}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm animate-fade"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className={`w-full max-w-lg overflow-hidden rounded-2xl border bg-[#101013] shadow-2xl animate-rise ${
          approving ? "border-[#e6b45c]/25" : "border-rose-400/20"
        }`}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-white/[0.06] px-6 py-4">
          <div className="flex items-center gap-3">
            <span
              className={`flex h-9 w-9 items-center justify-center rounded-xl border ${
                approving
                  ? "border-[#e6b45c]/30 bg-[#e6b45c]/[0.09] text-[#e6b45c]"
                  : "border-rose-400/25 bg-rose-400/[0.07] text-rose-300"
              }`}
            >
              {approving ? <IconShield size={16} /> : <IconX size={16} />}
            </span>
            <div>
              <h2 className="text-sm font-bold tracking-tight text-zinc-100">
                {approving ? "Authorize simulated correction" : "Reject proposed correction"}
              </h2>
              <p className="mt-0.5 select-all font-mono text-[10px] text-zinc-500">
                {detail.case.case_id}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={busy}
            aria-label="Close dialog"
            className="rounded-lg p-1.5 text-zinc-600 transition-colors hover:bg-white/[0.05] hover:text-zinc-300 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c]"
          >
            <IconX size={14} />
          </button>
        </div>

        {/* Body */}
        <div className="space-y-4 px-6 py-5">
          <p className="text-xs leading-relaxed text-zinc-400">
            {approving ? (
              <>
                You are authorizing an exact simulated ledger adjustment of{" "}
                <span className="font-mono font-semibold text-[#e6b45c]">{deltaText}</span>. This
                creates an immutable{" "}
                <span className="font-mono text-[11px] text-zinc-300">SIMULATED_CORRECTION</span>{" "}
                record against the sandbox ledger — imported entries are never modified and no
                external system is written.
              </>
            ) : (
              <>
                You are rejecting the proposed correction. The case will be preserved as{" "}
                <span className="font-semibold text-rose-300">UNRESOLVED</span> and the ledger will
                remain untouched.
              </>
            )}
          </p>

          {approving && detail.dry_run && (
            <dl className="grid grid-cols-3 gap-2 rounded-xl border border-white/[0.06] bg-black/35 p-3 text-center">
              <div>
                <dt className="text-[8.5px] font-semibold uppercase tracking-[0.14em] text-zinc-600">Before</dt>
                <dd className="mt-1 font-mono text-[11.5px] font-semibold text-[#e6b45c]">
                  {formatINR(detail.dry_run.variance_before_paise)}
                </dd>
              </div>
              <div>
                <dt className="text-[8.5px] font-semibold uppercase tracking-[0.14em] text-zinc-600">Delta</dt>
                <dd className="mt-1 font-mono text-[11.5px] font-semibold text-emerald-300">
                  {detail.dry_run.proposed_delta_paise < 0 ? "\u2212" : "+"}
                  {formatINR(Math.abs(detail.dry_run.proposed_delta_paise))}
                </dd>
              </div>
              <div>
                <dt className="text-[8.5px] font-semibold uppercase tracking-[0.14em] text-zinc-600">After</dt>
                <dd
                  className={`mt-1 font-mono text-[11.5px] font-semibold ${
                    detail.dry_run.variance_after_paise === 0 ? "text-emerald-300" : "text-rose-300"
                  }`}
                >
                  {formatINR(detail.dry_run.variance_after_paise)}
                </dd>
              </div>
            </dl>
          )}

          <div className="space-y-3">
            <label className="block">
              <span className="mb-1.5 block text-[9.5px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
                Reviewer identity
              </span>
              <input
                type="text"
                value={reviewerId}
                onChange={(e) => setReviewerId(e.target.value)}
                spellCheck={false}
                className="w-full rounded-xl border border-white/[0.08] bg-black/50 px-3.5 py-2.5 font-mono text-xs text-zinc-200 transition-colors placeholder-zinc-600 focus-visible:border-[#e6b45c]/40 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c]"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 flex items-baseline justify-between">
                <span className="text-[9.5px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
                  Reviewer notes
                </span>
                <span className="text-[9px] normal-case tracking-normal text-zinc-600">
                  optional · recorded verbatim in the audit trail
                </span>
              </span>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={3}
                placeholder="Your own justification for this decision…"
                className="w-full resize-none rounded-xl border border-white/[0.08] bg-black/50 px-3.5 py-2.5 text-xs leading-relaxed text-zinc-200 transition-colors placeholder-zinc-600 focus-visible:border-[#e6b45c]/40 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c]"
              />
            </label>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 border-t border-white/[0.06] bg-white/[0.015] px-6 py-4">
          <p className="hidden text-[9.5px] leading-relaxed text-zinc-600 sm:block">
            Voice is not an approval channel. This dialog is the only path.
          </p>
          <div className="flex gap-2.5">
            <button
              onClick={onClose}
              disabled={busy}
              className="rounded-xl border border-white/[0.08] bg-white/[0.02] px-4 py-2.5 text-xs font-semibold text-zinc-300 transition-colors hover:bg-white/[0.05] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c]"
            >
              Cancel
            </button>
            <button
              onClick={() => onConfirm(reviewerId.trim() || "reviewer-finance-ops", notes)}
              disabled={busy}
              autoFocus
              className={`flex items-center gap-2 rounded-xl px-5 py-2.5 text-xs font-bold tracking-wide text-black shadow-lg transition-all focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#e6b45c] active:scale-[0.99] disabled:cursor-wait disabled:opacity-60 ${
                approving
                  ? "bg-gradient-to-b from-[#f0c878] to-[#d9a24a] shadow-[#e6b45c]/25 hover:from-[#f4cf88]"
                  : "bg-gradient-to-b from-rose-300 to-rose-400 shadow-rose-900/40 hover:from-rose-200"
              }`}
            >
              {busy && (
                <span aria-hidden className="h-3 w-3 animate-spin rounded-full border-2 border-black/30 border-t-black/80" />
              )}
              {approving ? "Confirm authorization" : "Confirm rejection"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
