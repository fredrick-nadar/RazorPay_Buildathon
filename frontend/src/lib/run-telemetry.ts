import { formatRate } from "./format";
import type { RunListItem } from "./types";

type Summary = Record<string, unknown>;

function numberValue(object: Summary | undefined, key: string): number | undefined {
  const value = object?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}
function stringValue(object: Summary | undefined, key: string): string | undefined {
  const value = object?.[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function objectValue(object: Summary | undefined, key: string): Summary | undefined {
  const value = object?.[key];
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Summary)
    : undefined;
}

export interface RunTelemetry {
  runId: string;
  status: string;
  mode: string;
  eligible?: number;
  matched?: number;
  matchRate: string;
  casesCount?: number;
  quarantined?: number;
  residualVariance?: number;
  grossVolume?: number;
  recordsPerSecond?: number;
  totalSeconds?: number;
  economicOutputHash?: string;
}

/**
 * Build a display-only view of one persisted run summary.
 *
 * This intentionally exposes the runtime match rate, not evaluator precision.
 * Frozen benchmark accuracy is a separate, labelled artifact concern.
 */
export function telemetryFromRun(run: RunListItem): RunTelemetry {
  const summary = run.summary ?? {};
  const rate = objectValue(summary, "runtime_match_rate");
  const totals = objectValue(summary, "financial_control_totals");
  const timing = objectValue(summary, "timing_metrics");

  return {
    runId: run.run_id,
    status: run.status,
    mode: stringValue(summary, "mode") ?? "rules-only",
    eligible: numberValue(summary, "eligible_record_count"),
    matched: numberValue(summary, "matched_record_count"),
    matchRate: formatRate(
      numberValue(rate, "numerator") ?? Number.NaN,
      numberValue(rate, "denominator") ?? Number.NaN,
    ),
    casesCount: numberValue(summary, "cases_count"),
    quarantined: numberValue(summary, "quarantined_row_count"),
    residualVariance: numberValue(totals, "residual_abs_variance_paise"),
    grossVolume: numberValue(totals, "payment_gross_paise"),
    recordsPerSecond: numberValue(timing, "records_per_second"),
    totalSeconds: numberValue(timing, "total_seconds"),
    economicOutputHash:
      run.economic_output_hash ?? stringValue(summary, "economic_output_hash"),
  };
}
