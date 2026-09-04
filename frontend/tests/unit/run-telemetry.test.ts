import { describe, expect, it } from "vitest";

import { telemetryFromRun } from "../../src/lib/run-telemetry";
import type { RunListItem } from "../../src/lib/types";

function run(summary: Record<string, unknown>): RunListItem {
  return {
    run_id: "run-active",
    tenant_id: "argus-demo",
    inputs_path: "synthetic/inputs",
    status: "COMPLETED",
    started_at_utc: "2026-09-04T10:00:00+00:00",
    finished_at_utc: "2026-09-04T10:00:01+00:00",
    economic_output_hash: "authoritative-economic-hash",
    summary,
  };
}

describe("telemetryFromRun", () => {
  it("derives runtime metrics from one run without inventing evaluator precision", () => {
    const telemetry = telemetryFromRun(
      run({
        mode: "agent",
        eligible_record_count: 500,
        matched_record_count: 475,
        runtime_match_rate: { numerator: 475, denominator: 500 },
        cases_count: 25,
        quarantined_row_count: 2,
        financial_control_totals: {
          residual_abs_variance_paise: 3210,
          payment_gross_paise: 100_000,
        },
        timing_metrics: { records_per_second: 250, total_seconds: 2 },
        economic_output_hash: "stale-summary-hash",
      }),
    );

    expect(telemetry).toEqual({
      runId: "run-active",
      status: "COMPLETED",
      mode: "agent",
      eligible: 500,
      matched: 475,
      matchRate: "95.0%",
      casesCount: 25,
      quarantined: 2,
      residualVariance: 3210,
      grossVolume: 100_000,
      recordsPerSecond: 250,
      totalSeconds: 2,
      economicOutputHash: "authoritative-economic-hash",
    });
    expect(telemetry).not.toHaveProperty("precision");
    expect(telemetry).not.toHaveProperty("accuracy");
  });

  it("renders absent or invalid summary values as unavailable", () => {
    const telemetry = telemetryFromRun(run({ runtime_match_rate: { numerator: 0, denominator: 0 } }));

    expect(telemetry.matchRate).toBe("—");
    expect(telemetry.eligible).toBeUndefined();
    expect(telemetry.residualVariance).toBeUndefined();
  });
});
