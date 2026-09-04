interface RateMetric {
  numerator: number;
  denominator: number;
  rate: number;
}

export interface PublicBenchmarkArtifact {
  schema_version: "argus-public-benchmark-v1";
  source_artifact: string;
  source_sha256: string;
  source_digest_basis: "CANONICAL_JSON_V1";
  benchmark_version: string;
  dataset: string;
  mode: string;
  provider: string;
  eligible_records: number;
  match_precision: RateMetric;
  record_match_rate: RateMetric;
  case_classification: RateMetric;
  false_verifier_pass_count: number;
  duplicate_correction_count: number;
}

export interface BenchmarkStat {
  value: string;
  label: string;
  denominator: string;
}

function isRate(value: unknown): value is RateMetric {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<RateMetric>;
  return (
    typeof candidate.numerator === "number" &&
    Number.isInteger(candidate.numerator) &&
    typeof candidate.denominator === "number" &&
    Number.isInteger(candidate.denominator) &&
    candidate.denominator > 0 &&
    candidate.numerator >= 0 &&
    candidate.numerator <= candidate.denominator &&
    typeof candidate.rate === "number" &&
    Number.isFinite(candidate.rate)
  );
}

export function parsePublicBenchmark(value: unknown): PublicBenchmarkArtifact {
  if (!value || typeof value !== "object") throw new Error("benchmark summary is unavailable");
  const candidate = value as Partial<PublicBenchmarkArtifact>;
  if (
    candidate.schema_version !== "argus-public-benchmark-v1" ||
    typeof candidate.source_artifact !== "string" ||
    !candidate.source_artifact.endsWith("final.json") ||
    typeof candidate.source_sha256 !== "string" ||
    !/^[a-f0-9]{64}$/.test(candidate.source_sha256) ||
    candidate.source_digest_basis !== "CANONICAL_JSON_V1" ||
    typeof candidate.benchmark_version !== "string" ||
    typeof candidate.dataset !== "string" ||
    typeof candidate.mode !== "string" ||
    typeof candidate.provider !== "string" ||
    typeof candidate.eligible_records !== "number" ||
    !Number.isInteger(candidate.eligible_records) ||
    candidate.eligible_records < 0 ||
    !isRate(candidate.match_precision) ||
    !isRate(candidate.record_match_rate) ||
    !isRate(candidate.case_classification) ||
    typeof candidate.false_verifier_pass_count !== "number" ||
    !Number.isInteger(candidate.false_verifier_pass_count) ||
    candidate.false_verifier_pass_count < 0 ||
    typeof candidate.duplicate_correction_count !== "number" ||
    !Number.isInteger(candidate.duplicate_correction_count) ||
    candidate.duplicate_correction_count < 0
  ) {
    throw new Error("benchmark summary does not satisfy the public artifact contract");
  }
  return candidate as PublicBenchmarkArtifact;
}

function percent(rate: number, digits: number): string {
  return `${(rate * 100).toFixed(digits)}%`;
}

export function benchmarkStats(artifact: PublicBenchmarkArtifact): BenchmarkStat[] {
  return [
    {
      value: percent(artifact.record_match_rate.rate, 1),
      label: "Record match rate",
      denominator: `${artifact.record_match_rate.numerator.toLocaleString("en-IN")} / ${artifact.record_match_rate.denominator.toLocaleString("en-IN")} eligible records · frozen holdout`,
    },
    {
      value: percent(artifact.match_precision.rate, 1),
      label: "Match precision",
      denominator: `${artifact.match_precision.numerator.toLocaleString("en-IN")} / ${artifact.match_precision.denominator.toLocaleString("en-IN")} predicted relationships`,
    },
    {
      value: artifact.eligible_records.toLocaleString("en-IN"),
      label: "Eligible records evaluated",
      denominator: `${artifact.dataset.replace("datasets/", "")} synthetic dataset · measured batch`,
    },
    {
      value: artifact.false_verifier_pass_count.toLocaleString("en-IN"),
      label: "False verifier passes",
      denominator: `across ${artifact.case_classification.denominator.toLocaleString("en-IN")} labelled exception cases`,
    },
  ];
}
