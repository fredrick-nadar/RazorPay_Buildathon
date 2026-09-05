"use client";

/**
 * Five-way reconciled master matrix.
 *
 * This is the run's whole normalized inventory: payments, refunds,
 * settlements, bank entries and ledger entries, each row once, each with its
 * own link state. It previously showed only fully linked payments (84 of 282
 * records on the dev fixture) and labelled that subset "84 Matched Records",
 * so most of the evidence — and every unmatched row — was invisible.
 *
 * The component renders backend results only. Counts, link states and missing
 * links are all backend-derived; nothing here decides what is reconciled.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { formatCount, formatINR, formatUtc, shortHash } from "../lib/format";
import type { MatrixLinkState, MatrixPage, MatrixRecord, MatrixRecordType } from "../lib/types";
import { IconRoute, IconSearch, IconShield, IconX } from "./icons";

type LoadState = "IDLE" | "LOADING" | "READY" | "UNAVAILABLE" | "NOT_FOUND";

const RECORD_TYPES: Array<{ value: MatrixRecordType | "ALL"; label: string }> = [
  { value: "ALL", label: "All sources" },
  { value: "PAYMENT", label: "Payments" },
  { value: "REFUND", label: "Refunds" },
  { value: "SETTLEMENT", label: "Settlements" },
  { value: "BANK_ENTRY", label: "Bank entries" },
  { value: "LEDGER_ENTRY", label: "Ledger entries" },
];

const LINK_STATES: Array<{ value: MatrixLinkState | "ALL"; label: string }> = [
  { value: "ALL", label: "All rows" },
  { value: "RECONCILED", label: "Reconciled" },
  { value: "UNMATCHED", label: "Unmatched" },
];

const CENSUS_ORDER: MatrixRecordType[] = [
  "PAYMENT",
  "REFUND",
  "SETTLEMENT",
  "BANK_ENTRY",
  "LEDGER_ENTRY",
];

const TYPE_LABEL: Record<string, string> = {
  PAYMENT: "Payment",
  REFUND: "Refund",
  SETTLEMENT: "Settlement",
  BANK_ENTRY: "Bank entry",
  LEDGER_ENTRY: "Ledger entry",
};

const MISSING_LINK_LABEL: Record<string, string> = {
  NO_MATCH_GROUP: "no match group",
  NO_SETTLEMENT: "no settlement",
  NO_BANK_ENTRY: "no bank entry",
  NO_LEDGER_ENTRY: "no ledger entry",
  NON_UNIQUE_BANK_ENTRY: "multiple bank entries",
  NON_UNIQUE_LEDGER_ENTRY: "multiple ledger entries",
};

function isMatrixPage(value: unknown, expectedRunId: string): value is MatrixPage {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<MatrixPage>;
  const inventory = candidate.inventory;
  return (
    candidate.run_id === expectedRunId &&
    Array.isArray(candidate.records) &&
    typeof candidate.total === "number" &&
    typeof candidate.total_pages === "number" &&
    typeof inventory === "object" &&
    inventory !== null &&
    typeof inventory.total_records === "number" &&
    typeof inventory.reconciled_records === "number" &&
    typeof inventory.unmatched_records === "number" &&
    typeof inventory.by_record_type === "object" &&
    inventory.by_record_type !== null &&
    candidate.records.every(
      (record) =>
        typeof record?.record_id === "string" &&
        record.run_id === expectedRunId &&
        (record.link_state === "RECONCILED" || record.link_state === "UNMATCHED"),
    )
  );
}

export function MasterMatrixTable({ runId }: { runId: string | null }) {
  const [page, setPage] = useState<MatrixPage | null>(null);
  const [state, setState] = useState<LoadState>("IDLE");
  const [pageNumber, setPageNumber] = useState(1);
  const [limit, setLimit] = useState(25);
  const [search, setSearch] = useState("");
  const [recordType, setRecordType] = useState<MatrixRecordType | "ALL">("ALL");
  const [linkState, setLinkState] = useState<MatrixLinkState | "ALL">("ALL");
  const [activeTraceRecord, setActiveTraceRecord] = useState<MatrixRecord | null>(null);
  const requestId = useRef(0);

  const fetchMatrix = useCallback(
    async (targetRunId: string) => {
      // A monotonic generation means a slower earlier response can never
      // overwrite a newer page, filter or run.
      const generation = ++requestId.current;
      setState("LOADING");
      try {
        const params = new URLSearchParams({
          page: String(pageNumber),
          limit: String(limit),
          search: search.trim(),
          record_type: recordType,
          link_state: linkState,
        });
        const response = await fetch(
          `/api/v1/runs/${encodeURIComponent(targetRunId)}/matrix?${params.toString()}`,
        );
        if (generation !== requestId.current) return;
        if (response.status === 404) {
          setPage(null);
          setState("NOT_FOUND");
          return;
        }
        if (!response.ok) {
          setPage(null);
          setState("UNAVAILABLE");
          return;
        }
        const body: unknown = await response.json();
        if (generation !== requestId.current) return;
        if (!isMatrixPage(body, targetRunId)) {
          // Never keep the previous page and present it as current.
          setPage(null);
          setState("UNAVAILABLE");
          return;
        }
        setPage(body);
        setState("READY");
      } catch {
        if (generation === requestId.current) {
          setPage(null);
          setState("UNAVAILABLE");
        }
      }
    },
    [pageNumber, limit, search, recordType, linkState],
  );

  useEffect(() => {
    if (!runId) {
      requestId.current += 1;
      setPage(null);
      setState("IDLE");
      return;
    }
    void fetchMatrix(runId);
  }, [runId, fetchMatrix]);

  // Any filter change restarts at the first page.
  useEffect(() => {
    setPageNumber(1);
  }, [search, recordType, linkState, limit, runId]);

  const inventory = page?.inventory;
  const records = page?.records ?? [];
  const filtered = Boolean(search.trim()) || recordType !== "ALL" || linkState !== "ALL";

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-slate-50/40 p-4 sm:p-6">
      <div className="mb-4 flex flex-col gap-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-base font-bold tracking-tight text-slate-900">
              Five-way master record matrix
            </h2>
            <p className="mt-0.5 text-xs text-slate-500">
              Every normalized record in this run, with the links it does and does not have.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <div className="relative">
              <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400">
                <IconSearch size={14} />
              </span>
              <label className="sr-only" htmlFor="matrix-search">
                Search records by identifier
              </label>
              <input
                id="matrix-search"
                type="search"
                placeholder="Payment, refund, UTR, ledger…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="w-[220px] rounded-xl border border-slate-200 bg-white py-1.5 pl-8 pr-3 text-xs text-slate-900 shadow-2xs placeholder:text-slate-400 focus:border-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-300"
              />
            </div>

            <label className="sr-only" htmlFor="matrix-record-type">
              Filter by record type
            </label>
            <select
              id="matrix-record-type"
              value={recordType}
              onChange={(event) => setRecordType(event.target.value as MatrixRecordType | "ALL")}
              className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-2xs focus:outline-none focus:ring-2 focus:ring-slate-300"
            >
              {RECORD_TYPES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>

            <label className="sr-only" htmlFor="matrix-link-state">
              Filter by link state
            </label>
            <select
              id="matrix-link-state"
              value={linkState}
              onChange={(event) => setLinkState(event.target.value as MatrixLinkState | "ALL")}
              className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-2xs focus:outline-none focus:ring-2 focus:ring-slate-300"
            >
              {LINK_STATES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>

            <label className="sr-only" htmlFor="matrix-limit">
              Rows per page
            </label>
            <select
              id="matrix-limit"
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value))}
              className="rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-2xs focus:outline-none focus:ring-2 focus:ring-slate-300"
            >
              <option value={25}>25 / page</option>
              <option value={50}>50 / page</option>
              <option value={100}>100 / page</option>
              <option value={200}>200 / page</option>
            </select>
          </div>
        </div>

        {inventory && (
          <div
            data-testid="matrix-inventory"
            className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-2xl border border-slate-200 bg-white px-4 py-2.5"
          >
            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
              Run inventory
            </span>
            <span className="font-mono text-xs font-bold text-slate-900">
              {formatCount(inventory.total_records)} records
            </span>
            <span className="font-mono text-xs font-semibold text-emerald-700">
              {formatCount(inventory.reconciled_records)} reconciled
            </span>
            <span className="font-mono text-xs font-semibold text-amber-700">
              {formatCount(inventory.unmatched_records)} unmatched
            </span>
            <span aria-hidden className="h-4 w-px bg-slate-200" />
            {CENSUS_ORDER.map((kind) => {
              const bucket = inventory.by_record_type[kind];
              if (!bucket) return null;
              return (
                <button
                  key={kind}
                  type="button"
                  onClick={() => setRecordType(kind)}
                  className="rounded-lg border border-slate-200 px-2 py-0.5 text-[11px] font-medium text-slate-700 transition-colors hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-300"
                >
                  {TYPE_LABEL[kind]}{" "}
                  <span className="font-mono font-bold text-slate-900">
                    {formatCount(bucket.total)}
                  </span>
                  {bucket.unmatched > 0 && (
                    <span className="ml-1 font-mono text-amber-700">
                      ({formatCount(bucket.unmatched)} unmatched)
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xs">
        <div className="flex-1 overflow-auto">
          <table className="w-full border-collapse text-left text-xs">
            <caption className="sr-only">
              Normalized records for the selected run with reconciliation link state
            </caption>
            <thead className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50/95 font-semibold text-slate-700 backdrop-blur-xs">
              <tr>
                {["Record", "Type", "Signed amount", "Occurred", "Links", "Source", "Trace"].map(
                  (heading) => (
                    <th
                      key={heading}
                      scope="col"
                      className={`px-3 py-3 text-[10px] font-medium uppercase tracking-wider text-slate-500 ${
                        heading === "Trace" ? "text-right" : ""
                      }`}
                    >
                      {heading}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100" aria-live="polite">
              {state === "IDLE" ? (
                <EmptyRow
                  title="No run selected"
                  detail="The matrix is scoped to one reconciliation run. Import evidence to create the first run."
                />
              ) : state === "LOADING" && !page ? (
                <EmptyRow title="Loading run inventory…" detail="Reading normalized records." />
              ) : state === "NOT_FOUND" ? (
                <EmptyRow
                  title="This run no longer exists"
                  detail="The selected run could not be found, so no records are shown."
                />
              ) : state === "UNAVAILABLE" ? (
                <EmptyRow
                  title="Matrix data is unavailable"
                  detail="The previous page is not treated as current. Retry when the backend is reachable."
                  action={
                    runId ? (
                      <button
                        type="button"
                        onClick={() => void fetchMatrix(runId)}
                        className="mt-3 rounded-lg border border-slate-900 bg-white px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-900 hover:bg-slate-50"
                      >
                        Retry matrix
                      </button>
                    ) : null
                  }
                />
              ) : records.length === 0 ? (
                <EmptyRow
                  title={filtered ? "No records match this filter" : "This run holds no records"}
                  detail={
                    filtered
                      ? "Clear the search or choose a different source to see the full inventory."
                      : "The run completed without any normalized records."
                  }
                />
              ) : (
                records.map((record) => (
                  <tr
                    key={`${record.record_type}:${record.record_id}`}
                    className="group cursor-pointer transition-colors hover:bg-slate-50/80"
                    onClick={() => setActiveTraceRecord(record)}
                  >
                    <td className="px-3 py-2.5">
                      <span className="select-all font-mono text-[11px] font-bold text-slate-900">
                        {record.record_id}
                      </span>
                      {record.order_id ? (
                        <span className="block font-mono text-[10px] text-slate-400">
                          {record.order_id}
                        </span>
                      ) : null}
                    </td>

                    <td className="px-3 py-2.5">
                      <span className="rounded border border-slate-200 bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-slate-700">
                        {TYPE_LABEL[record.record_type] ?? record.record_type}
                      </span>
                      {record.status ? (
                        <span className="block text-[10px] text-slate-400">{record.status}</span>
                      ) : null}
                    </td>

                    <td className="px-3 py-2.5 font-mono font-bold tabular-nums text-slate-900">
                      {formatINR(record.signed_amount_paise)}
                      {record.fee_paise !== undefined && record.tax_paise !== undefined ? (
                        <span className="block text-[10px] font-normal text-slate-400">
                          fee {formatINR(record.fee_paise)} · tax {formatINR(record.tax_paise)}
                        </span>
                      ) : null}
                    </td>

                    <td className="px-3 py-2.5 font-mono text-[10.5px] text-slate-500">
                      {record.occurred_at_utc ? formatUtc(record.occurred_at_utc) : "—"}
                    </td>

                    <td className="px-3 py-2.5">
                      {record.link_state === "RECONCILED" ? (
                        <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-emerald-800">
                          Reconciled
                        </span>
                      ) : (
                        <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-800">
                          Unmatched
                        </span>
                      )}
                      {record.missing_links.length > 0 ? (
                        <span className="block text-[10px] text-slate-500">
                          {record.missing_links
                            .map((code) => MISSING_LINK_LABEL[code] ?? code.toLowerCase())
                            .join(" · ")}
                        </span>
                      ) : record.match_rule ? (
                        <span className="block font-mono text-[10px] text-slate-400">
                          {record.match_rule}
                        </span>
                      ) : null}
                    </td>

                    <td className="px-3 py-2.5 font-mono text-[10px] text-slate-500">
                      row {record.source_row_number}
                      {record.content_hash ? (
                        <span className="block" title={record.content_hash}>
                          {shortHash(record.content_hash, 10)}
                        </span>
                      ) : null}
                    </td>

                    <td className="px-3 py-2.5 text-right">
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          setActiveTraceRecord(record);
                        }}
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-700 shadow-2xs transition-colors hover:bg-slate-100 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-300"
                      >
                        <IconRoute size={12} className="text-slate-600" />
                        <span>Trace</span>
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {page && (
          <div className="flex items-center justify-between border-t border-slate-200 bg-slate-50 px-4 py-2.5">
            <p className="text-xs text-slate-500">
              Showing{" "}
              <span className="font-semibold text-slate-800">
                {records.length > 0 ? (page.page - 1) * page.limit + 1 : 0}
              </span>{" "}
              to{" "}
              <span className="font-semibold text-slate-800">
                {Math.min(page.page * page.limit, page.total)}
              </span>{" "}
              of <span className="font-semibold text-slate-800">{formatCount(page.total)}</span>{" "}
              {filtered ? "matching" : "inventory"} records
            </p>

            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => setPageNumber((current) => Math.max(1, current - 1))}
                disabled={page.page <= 1}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 shadow-2xs transition-colors hover:bg-slate-50 disabled:opacity-40"
              >
                Previous
              </button>
              <span className="px-2 font-mono text-xs font-bold text-slate-700">
                Page {page.page} / {page.total_pages}
              </span>
              <button
                type="button"
                onClick={() =>
                  setPageNumber((current) => Math.min(page.total_pages, current + 1))
                }
                disabled={page.page >= page.total_pages}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 shadow-2xs transition-colors hover:bg-slate-50 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>

      {activeTraceRecord && (
        <TraceGraphModal
          record={activeTraceRecord}
          onClose={() => setActiveTraceRecord(null)}
        />
      )}
    </div>
  );
}

function EmptyRow({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: React.ReactNode;
}) {
  return (
    <tr>
      <td colSpan={7} className="p-8 text-center">
        <p className="text-sm font-bold text-slate-800">{title}</p>
        <p className="mx-auto mt-1 max-w-md text-xs text-slate-500">{detail}</p>
        {action}
      </td>
    </tr>
  );
}

/* ------------------------------------------------------------------ */
/* Trace modal                                                         */
/* ------------------------------------------------------------------ */

interface TraceLink {
  label: string;
  value: string | null;
  amountPaise?: number | null;
}

function traceLinks(record: MatrixRecord): TraceLink[] {
  switch (record.record_type) {
    case "PAYMENT":
      return [
        { label: "Payment", value: record.record_id, amountPaise: record.gross_amount_paise },
        { label: "Order", value: record.order_id ?? null },
        {
          label: "Settlement",
          value: record.settlement_id ?? null,
          amountPaise: record.settlement_gross_paise ?? null,
        },
        { label: "Bank UTR", value: record.utr ?? null, amountPaise: record.bank_amount_paise ?? null },
        {
          label: "Ledger entry",
          value: record.ledger_entry_id ?? null,
          amountPaise: record.ledger_amount_paise ?? null,
        },
      ];
    case "REFUND":
      return [
        { label: "Refund", value: record.record_id, amountPaise: record.signed_amount_paise },
        { label: "Original payment", value: record.payment_id ?? null },
        { label: "Settlement", value: record.settlement_id ?? null },
      ];
    case "SETTLEMENT":
      return [
        { label: "Settlement", value: record.record_id, amountPaise: record.signed_amount_paise },
        { label: "Gross credit", value: null, amountPaise: record.gross_credit_paise ?? null },
        { label: "Bank UTR", value: record.utr ?? null },
        { label: "Bank entry", value: record.bank_entry_id ?? null },
      ];
    case "BANK_ENTRY":
      return [
        { label: "Bank entry", value: record.record_id, amountPaise: record.signed_amount_paise },
        { label: "UTR", value: record.utr ?? null },
        { label: "Value date", value: record.value_date ?? null },
      ];
    case "LEDGER_ENTRY":
      return [
        { label: "Ledger entry", value: record.record_id, amountPaise: record.signed_amount_paise },
        { label: "Account", value: record.account_code ?? null },
        { label: "Source reference", value: record.source_reference ?? null },
        { label: "Origin", value: record.entry_origin ?? null },
      ];
    default:
      return [{ label: "Record", value: record.record_id, amountPaise: record.signed_amount_paise }];
  }
}

function TraceGraphModal({
  record,
  onClose,
}: {
  record: MatrixRecord;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const links = traceLinks(record);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Close trace"
        onClick={onClose}
        className="fixed inset-0 bg-slate-950/50 backdrop-blur-[2px]"
      />
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="trace-title"
        className="relative z-10 max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-slate-200 bg-white shadow-2xl"
      >
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            <h3 id="trace-title" className="text-sm font-bold text-slate-900">
              Record trace
            </h3>
            <p className="mt-0.5 select-all font-mono text-[11px] text-slate-500">
              {record.record_id} · run {record.run_id}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
          >
            <IconX size={15} />
          </button>
        </header>

        <div className="space-y-4 p-5">
          <p
            className={`flex items-start gap-2 rounded-xl border p-3 text-xs font-medium ${
              record.link_state === "RECONCILED"
                ? "border-emerald-200 bg-emerald-50/60 text-emerald-900"
                : "border-amber-200 bg-amber-50/60 text-amber-900"
            }`}
          >
            <IconShield size={15} className="mt-px shrink-0" />
            <span>
              {record.link_state === "RECONCILED" ? (
                <>
                  Linked by persisted match evidence
                  {record.match_rule ? (
                    <> under <span className="font-mono">{record.match_rule}</span></>
                  ) : null}
                  . The related identifiers below come from stored source references.
                </>
              ) : (
                <>
                  This record is unmatched. Missing:{" "}
                  {record.missing_links
                    .map((code) => MISSING_LINK_LABEL[code] ?? code.toLowerCase())
                    .join(", ") || "unknown"}
                  . It is reported as-is; nothing is inferred to complete the chain.
                </>
              )}
            </span>
          </p>

          <ol className="space-y-2">
            {links.map((link, index) => (
              <li
                key={`${link.label}-${index}`}
                className="flex items-baseline justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50/70 px-3.5 py-2.5"
              >
                <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
                  {link.label}
                </span>
                <span className="min-w-0 text-right">
                  <span className="block select-all truncate font-mono text-xs font-bold text-slate-900">
                    {link.value ?? "not present"}
                  </span>
                  {link.amountPaise !== undefined && link.amountPaise !== null ? (
                    <span className="block font-mono text-[10px] tabular-nums text-slate-500">
                      {formatINR(link.amountPaise)}
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
          </ol>

          <footer className="flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-100 pt-3 font-mono text-[10px] text-slate-500">
            <span>source row {record.source_row_number}</span>
            {record.content_hash ? (
              <span title={record.content_hash}>content {shortHash(record.content_hash, 16)}</span>
            ) : null}
            {record.occurred_at_utc ? <span>{formatUtc(record.occurred_at_utc)}</span> : null}
          </footer>
        </div>
      </section>
    </div>
  );
}
