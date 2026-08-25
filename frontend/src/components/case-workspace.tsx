/**
 * Case workspace: investigation dossier — summary, hypotheses, proof
 * package, dry-run ledger preview, and the authority action surface.
 * Clean, minimal, bright & professional.
 */

"use client";

import { CaseStatus } from "../domain/enums";
import type { CaseDetail } from "../lib/types";
import { formatINR, formatUtc } from "../lib/format";
import {
  IconCheck,
  IconScale,
  IconScroll,
  IconShield,
  IconX,
} from "./icons";
import { Panel, SectionLabel, Badge, CopyChip } from "./primitives";
import { categoryMeta, StatusBadge } from "./case-rail";

/* ------------------------------------------------------------------ */
/* Overview                                                            */
/* ------------------------------------------------------------------ */

function Figure({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "brass" | "positive";
}) {
  const color =
    tone === "brass"
      ? "text-amber-700"
      : tone === "positive"
        ? "text-emerald-700"
        : "text-slate-900";
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/80 px-3.5 py-3 shadow-none">
      <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
        {label}
      </div>
      <div className={`mt-1.5 font-mono text-[16px] font-bold tabular-nums tracking-tight ${color}`}>
        {value}
      </div>
    </div>
  );
}

function CaseOverview({ detail }: { detail: CaseDetail }) {
  const c = detail.case;
  const cat = categoryMeta(c.category);
  return (
    <Panel className="p-5">
      <SectionLabel right={<StatusBadge status={c.status} />}>
        Case dossier
      </SectionLabel>

      <p className="mt-3 text-sm leading-relaxed text-slate-700">{c.summary}</p>

      <div className="mt-4 grid grid-cols-1 gap-2.5 sm:grid-cols-3">
        <Figure label="Initial variance" value={formatINR(c.variance_paise)} tone="brass" />
        <Figure label="Affected amount" value={formatINR(c.affected_amount_paise)} />
        <Figure
          label="Proposed delta"
          value={c.proposed_delta_paise !== null ? formatINR(c.proposed_delta_paise) : "\u2014"}
          tone={c.proposed_delta_paise !== null ? "positive" : "default"}
        />
      </div>

      {(c.reason_codes.length > 0 || c.evidence.length > 0) && (
        <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-slate-100 pt-3 text-xs">
          <span className="inline-flex items-center gap-1.5 font-semibold" style={{ color: cat.hex }}>
            {cat.icon}
            <span className="uppercase tracking-wider">{cat.label}</span>
          </span>
          <span className="font-mono text-slate-500">opened {formatUtc(c.opened_at_utc)}</span>
          <span className="font-mono text-slate-500">updated {formatUtc(c.updated_at_utc)}</span>
          {c.reason_codes.map((rc) => (
            <Badge key={rc} tone="neutral">
              {rc}
            </Badge>
          ))}
        </div>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
/* Hypotheses                                                          */
/* ------------------------------------------------------------------ */

type HypBadgeTone = "positive" | "critical" | "warning" | "neutral";

function hypMeta(status: string): { tone: HypBadgeTone; label: string } {
  switch (status) {
    case "SUPPORTED":
      return { tone: "positive", label: "Supported" };
    case "REJECTED":
      return { tone: "critical", label: "Rejected" };
    case "INCONCLUSIVE":
      return { tone: "warning", label: "Inconclusive" };
    case "PROPOSED":
      return { tone: "neutral", label: "Proposed" };
    default:
      return { tone: "neutral", label: humanize(status) || status };
  }
}

function Hypotheses({ detail }: { detail: CaseDetail }) {
  return (
    <Panel className="p-5">
      <SectionLabel
        right={
          <span className="text-[10.5px] font-medium text-slate-500">
            Falsified deterministically · No model arithmetic
          </span>
        }
      >
        Competing hypotheses ({detail.hypotheses.length})
      </SectionLabel>

      {detail.hypotheses.length === 0 ? (
        <p className="mt-4 text-xs leading-relaxed text-slate-500">
          No hypothesis was recorded for this case.
        </p>
      ) : (
        <ul className="mt-3.5 space-y-2.5">
          {detail.hypotheses.map((h) => {
            const meta = hypMeta(h.status);
            return (
              <li
                key={h.hypothesis_id}
                className="flex items-start justify-between gap-4 rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-3 transition-colors hover:border-slate-300 hover:bg-slate-50"
              >
                <div className="min-w-0 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="select-all font-mono text-xs font-bold text-blue-700">
                      {h.hypothesis_id}
                    </span>
                    <span aria-hidden className="text-slate-300">·</span>
                    <span className="font-mono text-[10px] font-bold uppercase tracking-wider text-slate-500">
                      {humanize(h.category)}
                    </span>
                  </div>
                  <p className="text-xs font-medium leading-relaxed text-slate-800">{h.claim}</p>
                  {h.reason_codes.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 pt-0.5">
                      {h.reason_codes.map((rc) => (
                        <Badge key={rc} tone="neutral">
                          {rc}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
                <Badge tone={meta.tone}>{meta.label}</Badge>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

function humanize(value: string): string {
  return value.toLowerCase().replaceAll("_", " ");
}

/* ------------------------------------------------------------------ */
/* Proof package                                                       */
/* ------------------------------------------------------------------ */

function ProofCard({ detail }: { detail: CaseDetail }) {
  const proof = detail.proof;
  if (!proof) {
    return (
      <Panel className="p-5">
        <SectionLabel accent>
          <IconShield size={13} /> Proof package
        </SectionLabel>
        <p className="mt-4 text-xs leading-relaxed text-slate-500">
          No deterministic proof exists yet. A case cannot be resolved without a
          verifier PASS — ambiguity is preserved, never overridden.
        </p>
      </Panel>
    );
  }

  const pass = proof.verifier_status === "PASS";
  return (
    <Panel
      className={`p-5 ${pass ? "border-emerald-200" : "border-slate-200"}`}
    >
      <SectionLabel
        accent={pass}
        right={
          <Badge tone={pass ? "positive" : proof.verifier_status === "INCONCLUSIVE" ? "warning" : "critical"}>
            Verifier {proof.verifier_status}
          </Badge>
        }
      >
        <IconShield size={13} /> Proof package
      </SectionLabel>

      <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 rounded-xl border border-slate-200 bg-slate-50/70 p-4 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Verifier rule</dt>
          <dd className="mt-1 select-all break-all font-mono font-semibold text-slate-800">
            {proof.verifier_rule_id}
            <span className="ml-1.5 rounded bg-slate-200/80 px-1 py-0.5 text-[10px] text-slate-700">
              {proof.verifier_rule_version}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Authority decision</dt>
          <dd className="mt-1 font-mono font-semibold uppercase tracking-wide text-slate-800">
            {proof.authority_decision.replaceAll("_", " ")}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
            Supported evidence ({proof.supported_evidence.length})
          </dt>
          <dd className="mt-1 select-all break-all font-mono text-[11px] font-semibold leading-relaxed text-emerald-700">
            {proof.supported_evidence.join("  ") || "\u2014"}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
            Conflicting evidence ({proof.conflicting_evidence.length})
          </dt>
          <dd className="mt-1 select-all break-all font-mono text-[11px] font-semibold leading-relaxed text-rose-700">
            {proof.conflicting_evidence.join("  ") || "\u2014"}
          </dd>
        </div>
      </dl>

      {proof.rejected_alternatives.length > 0 && (
        <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50/50 p-3.5">
          <div className="text-[10px] font-bold uppercase tracking-wider text-rose-800">
            Rejected alternatives ({proof.rejected_alternatives.length})
          </div>
          <ul className="mt-2 space-y-1">
            {proof.rejected_alternatives.map((alt, i) => (
              <li key={i} className="flex items-center gap-2 font-mono text-xs text-rose-900">
                <IconX size={11} className="shrink-0 text-rose-600" />
                <span className="truncate">{JSON.stringify(alt)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {proof.equations.length > 0 && (
        <div className="mt-3 rounded-xl border border-blue-200 bg-blue-50/50 p-3.5">
          <div className="text-[10px] font-bold uppercase tracking-wider text-blue-800">
            Deterministic equations ({proof.equations.length})
          </div>
          <ul className="mt-2 space-y-1.5">
            {proof.equations.map((eq, i) => (
              <li key={i} className="select-all break-all font-mono text-xs font-semibold leading-relaxed text-slate-800">
                {JSON.stringify(eq)}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-2.5">
        <div className="min-w-0">
          <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
            Canonical hash · SHA-256
          </div>
          <div className="mt-0.5 select-all truncate font-mono text-xs text-slate-700" title={proof.canonical_hash}>
            {proof.canonical_hash}
          </div>
        </div>
        <CopyChip value={proof.canonical_hash} label="Copy" />
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
/* Dry-run ledger console                                              */
/* ------------------------------------------------------------------ */

function DryRunConsole({
  detail,
  onApprove,
  onReject,
}: {
  detail: CaseDetail;
  onApprove: () => void;
  onReject: () => void;
  }) {
  const c = detail.case;
  const dry = detail.dry_run;
  const sim = detail.simulated_correction;

  return (
    <div className="space-y-5">
      <Panel className="p-5">
        <SectionLabel>
          <IconScale size={13} /> Ledger dry-run
        </SectionLabel>

        {!dry && (
          <p className="mt-4 text-xs leading-relaxed text-slate-500">
            No dry-run preview exists for this case. Corrections are simulated
            against the sandbox ledger only after a verifier PASS.
          </p>
        )}

        {dry && (
          <>
            {/* Before → after */}
            <div className="mt-4 overflow-hidden rounded-xl border border-slate-200">
              <div className="grid grid-cols-[1fr_auto_1fr] items-stretch divide-x divide-slate-200 bg-slate-50">
                <div className="px-4 py-3.5 text-center">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
                    Variance before
                  </div>
                  <div className="mt-1.5 font-mono text-lg font-bold tabular-nums text-amber-700">
                    {formatINR(dry.variance_before_paise)}
                  </div>
                </div>
                <div className="flex items-center px-2 text-slate-400">
                  <svg width="22" height="12" viewBox="0 0 22 12" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
                    <path d="M0 6h17" />
                    <path d="m14 1.5 4.5 4.5L14 10.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </div>
                <div className="px-4 py-3.5 text-center">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
                    Variance after
                  </div>
                  <div
                    className={`mt-1.5 font-mono text-lg font-bold tabular-nums ${
                      dry.variance_after_paise === 0 ? "text-emerald-700" : "text-rose-700"
                    }`}
                  >
                    {formatINR(dry.variance_after_paise)}
                  </div>
                </div>
              </div>
              <div className="flex items-center justify-between border-t border-slate-200 bg-white px-4 py-2">
                <span className="text-[10.5px] font-bold uppercase tracking-wider text-slate-500">
                  Proposed signed entry
                </span>
                <span
                  className={`font-mono text-xs font-bold ${
                    dry.proposed_delta_paise < 0 ? "text-rose-700" : "text-emerald-700"
                  }`}
                >
                  {dry.proposed_delta_paise < 0 ? "\u2212" : "+"}
                  {formatINR(Math.abs(dry.proposed_delta_paise)).replace("\u2212", "")}
                  {dry.account_code && (
                    <span className="ml-2 select-all rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-700">
                      {dry.account_code}
                    </span>
                  )}
                </span>
              </div>
            </div>

            {dry.warnings.length > 0 && (
              <ul className="mt-3 space-y-1">
                {dry.warnings.map((w, i) => (
                  <li key={i} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900">
                    {w}
                  </li>
                ))}
              </ul>
            )}

            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-slate-500">
              <span>preview {formatUtc(dry.created_at_utc)}</span>
              <span>simulation only · no external writes</span>
            </div>
          </>
        )}

        {/* Authority actions */}
        {c.status === CaseStatus.APPROVAL_REQUIRED && (
          <div className="mt-5 space-y-2.5 border-t border-slate-100 pt-4">
            <button
              onClick={onApprove}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-slate-900 py-3 text-xs font-bold tracking-wide text-white shadow-sm transition-all hover:bg-slate-800 active:scale-[0.99]"
            >
              <IconShield size={14} />
              Authorize &amp; apply simulated correction
            </button>
            <button
              onClick={onReject}
              className="w-full rounded-xl border border-slate-200 bg-white py-2.5 text-xs font-semibold text-slate-700 transition-colors hover:border-rose-300 hover:bg-rose-50 hover:text-rose-700"
            >
              Reject · preserve as unresolved
            </button>
            <p className="pt-1 text-center text-[10px] font-medium leading-relaxed tracking-wide text-slate-500">
              Human authorization is required for every non-zero ledger delta.
            </p>
          </div>
        )}

        {c.status === CaseStatus.SIMULATED_APPLIED && sim && (
          <div className="mt-5 space-y-2.5 rounded-xl border border-purple-200 bg-purple-50/70 p-4">
            <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-purple-900">
              <IconCheck size={14} className="text-purple-700" />
              Simulated correction applied
            </div>
            <dl className="space-y-1 font-mono text-xs text-purple-950">
              <div className="flex justify-between gap-3">
                <dt className="text-purple-700">Correction</dt>
                <dd className="select-all truncate font-semibold">{sim.correction_id}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-purple-700">Approval</dt>
                <dd className="select-all truncate font-semibold">{sim.approval_id}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-purple-700">Applied</dt>
                <dd>{formatUtc(sim.applied_at_utc)}</dd>
              </div>
            </dl>
            <p className="border-t border-purple-200/60 pt-2 text-[10.5px] leading-relaxed text-purple-800">
              A new linked SIMULATED_CORRECTION entry was created. Imported
              records remain immutable.
            </p>
          </div>
        )}

        {c.status === CaseStatus.UNRESOLVED && (
          <div className="mt-5 rounded-xl border border-rose-200 bg-rose-50/80 p-4">
            <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-rose-900">
              <FlagGlyph />
              Preserved as unresolved
            </div>
            <p className="mt-2 text-xs leading-relaxed text-rose-950 font-medium">
              The evidence does not uniquely identify one valid explanation.
              ARGUS does not guess — this case stays open for human inspection
              rather than forcing an unverifiable closure.
            </p>
          </div>
        )}

        {(c.status === CaseStatus.VERIFIED_RESOLVED || c.status === CaseStatus.INVESTIGATING || c.status === CaseStatus.OPEN) && (
          <div className="mt-4 flex items-center gap-2 border-t border-slate-100 pt-3 text-[11px] font-medium text-slate-500">
            <IconScroll size={13} />
            <span>Status: {c.status.replaceAll("_", " ").toLowerCase()} · awaiting deterministic pipeline progress</span>
          </div>
        )}
      </Panel>
    </div>
  );
}

function FlagGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M5 21V4.5C8 3 10.5 3 12 4.5s4 1.5 7 0V15c-3 1.5-5.5 1.5-7 0s-4-1.5-7 0" />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Composition                                                         */
/* ------------------------------------------------------------------ */

export function CaseWorkspace({
  detail,
  onApprove,
  onReject,
}: {
  detail: CaseDetail;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.65fr)_minmax(320px,1fr)]">
      <div className="space-y-5">
        <CaseOverview detail={detail} />
        <Hypotheses detail={detail} />
        <ProofCard detail={detail} />
      </div>
      <div className="space-y-5 xl:sticky xl:top-5 xl:self-start">
        <DryRunConsole detail={detail} onApprove={onApprove} onReject={onReject} />
      </div>
    </div>
  );
}
