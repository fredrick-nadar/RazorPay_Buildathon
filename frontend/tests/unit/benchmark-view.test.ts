import { describe, expect, it } from "vitest";

import { benchmarkStats, parsePublicBenchmark } from "../../src/lib/benchmark-view";

const artifact = {
  schema_version: "argus-public-benchmark-v1",
  source_artifact: "artifacts/benchmark/final.json",
  source_sha256: "a".repeat(64),
  source_digest_basis: "CANONICAL_JSON_V1",
  benchmark_version: "benchmark-v1",
  dataset: "datasets/holdout",
  mode: "agent",
  provider: "fake",
  eligible_records: 600,
  match_precision: { numerator: 400, denominator: 400, rate: 1 },
  record_match_rate: { numerator: 570, denominator: 600, rate: 0.95 },
  case_classification: { numerator: 20, denominator: 20, rate: 1 },
  false_verifier_pass_count: 0,
  duplicate_correction_count: 0,
} as const;

describe("public benchmark view", () => {
  it("derives every displayed result from the generated artifact", () => {
    const parsed = parsePublicBenchmark(artifact);
    expect(benchmarkStats(parsed)).toEqual([
      {
        value: "95.0%",
        label: "Record match rate",
        denominator: "570 / 600 eligible records · frozen holdout",
      },
      {
        value: "100.0%",
        label: "Match precision",
        denominator: "400 / 400 predicted relationships",
      },
      {
        value: "600",
        label: "Eligible records evaluated",
        denominator: "holdout synthetic dataset · measured batch",
      },
      {
        value: "0",
        label: "False verifier passes",
        denominator: "across 20 labelled exception cases",
      },
    ]);
  });

  it("rejects a summary without a source digest", () => {
    expect(() => parsePublicBenchmark({ ...artifact, source_sha256: "missing" })).toThrow(
      "public artifact contract",
    );
  });
});
