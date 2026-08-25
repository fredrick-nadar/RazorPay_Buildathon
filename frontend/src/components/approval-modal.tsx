/**
 * Human authority modal. Approval and rejection are explicit user actions.
 * Clean, minimal, bright & professional.
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
      className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm animate-fade"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="w-full max-w-lg overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl animate-rise text-slate-900"
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <span
              className={`flex h-9 w-9 items-center justify-center rounded-xl border ${
                approving
                  ? "border-blue-200 bg-blue-50 text-blue-700"
                  : "border-rose-200 bg-rose-50 text-rose-700"
              }`}
            >
              {approving ? <IconShield size={16} /> : <IconX size={16} />}
            </span>
            <div>
              <h2 className="text-sm font-bold tracking-tight text-slate-900">
                {approving ? "Authorize simulated correction" : "Reject proposed correction"}
              </h2>
              <p className="mt-0.5 select-all font-mono text-xs font-semibold text-slate-500">
                {detail.case.case_id}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={busy}
            aria-label="Close dialog"
            className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700"
          >
            <IconX size={15} />
          </button>
        </div>

        {/* Body */}
        <div className="space-y-4 px-6 py-5">
          <p className="text-xs font-medium leading-relaxed text-slate-600">
            {approving ? (
              <>
                You are authorizing an exact simulated ledger adjustment of{" "}
                <span className="font-mono font-bold text-slate-900">{deltaText}</span>. This
                creates an immutable{" "}
                <span className="font-mono text-xs font-semibold text-slate-900">SIMULATED_CORRECTION</span>{" "}
                record against the sandbox ledger — imported entries are never modified and no
                external system is written.
              </>
            ) : (
              <>
                You are rejecting the proposed correction. The case will be preserved as{" "}
                <span className="font-bold text-rose-700">UNRESOLVED</span> and the ledger will
                remain untouched.
              </>
            )}
          </p>

          {approving && detail.dry_run && (
            <dl className="grid grid-cols-3 gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 text-center">
              <div>
                <dt className="text-[9px] font-bold uppercase tracking-wider text-slate-500">Before</dt>
                <dd className="mt-1 font-mono text-xs font-bold text-amber-700">
                  {formatINR(detail.dry_run.variance_before_paise)}
                </dd>
              </div>
              <div>
                <dt className="text-[9px] font-bold uppercase tracking-wider text-slate-500">Delta</dt>
                <dd className="mt-1 font-mono text-xs font-bold text-emerald-700">
                  {detail.dry_run.proposed_delta_paise < 0 ? "\u2212" : "+"}
                  {formatINR(Math.abs(detail.dry_run.proposed_delta_paise))}
                </dd>
              </div>
              <div>
                <dt className="text-[9px] font-bold uppercase tracking-wider text-slate-500">After</dt>
                <dd
                  className={`mt-1 font-mono text-xs font-bold ${
                    detail.dry_run.variance_after_paise === 0 ? "text-emerald-700" : "text-rose-700"
                  }`}
                >
                  {formatINR(detail.dry_run.variance_after_paise)}
                </dd>
              </div>
            </dl>
          )}

          <div className="space-y-3">
            <label className="block">
              <span className="mb-1.5 block text-[10px] font-bold uppercase tracking-wider text-slate-600">
                Reviewer identity
              </span>
              <input
                type="text"
                value={reviewerId}
                onChange={(e) => setReviewerId(e.target.value)}
                spellCheck={false}
                className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-2.5 font-mono text-xs font-semibold text-slate-900 transition-colors focus:border-slate-400 focus:bg-white focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 flex items-baseline justify-between">
                <span className="text-[10px] font-bold uppercase tracking-wider text-slate-600">
                  Reviewer notes
                </span>
                <span className="text-[10px] text-slate-400">
                  Optional · Recorded verbatim in audit trail
                </span>
              </span>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={3}
                placeholder="Justification for this decision..."
                className="w-full resize-none rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-xs font-medium text-slate-900 placeholder-slate-400 transition-colors focus:border-slate-400 focus:bg-white focus:outline-none"
              />
            </label>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 border-t border-slate-100 bg-slate-50/50 px-6 py-4">
          <p className="hidden text-[10px] font-medium text-slate-500 sm:block">
            Voice is not an approval channel. This dialog is the only path.
          </p>
          <div className="flex gap-2.5">
            <button
              onClick={onClose}
              disabled={busy}
              className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-semibold text-slate-700 transition-colors hover:bg-slate-100"
            >
              Cancel
            </button>
            <button
              onClick={() => onConfirm(reviewerId.trim() || "reviewer-finance-ops", notes)}
              disabled={busy}
              autoFocus
              className={`flex items-center gap-2 rounded-xl px-5 py-2 text-xs font-bold tracking-wide text-white shadow-sm transition-all active:scale-[0.99] disabled:cursor-wait disabled:opacity-60 ${
                approving
                  ? "bg-slate-900 hover:bg-slate-800"
                  : "bg-rose-600 hover:bg-rose-700"
              }`}
            >
              {busy && (
                <span aria-hidden className="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              )}
              {approving ? "Confirm authorization" : "Confirm rejection"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
