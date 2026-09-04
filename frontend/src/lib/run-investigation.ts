/**
 * Honest reporting of what an investigation actually achieved.
 *
 * A financial run can complete safely while every AI investigation failed:
 * deterministic reconciliation and verification are unaffected, and failed
 * cases simply stay unresolved. That outcome must never be displayed as a
 * fully investigated run.
 *
 * Two provider sets matter and must not be conflated:
 *   attempted_providers - every backend dialled, timed-out ones included
 *   actual_providers    - backends that RETURNED a completed model turn
 * An empty actual set with a non-empty attempted set means no model answered.
 */

export type InvestigationStatus =
  | "FULLY_INVESTIGATED"
  | "COMPLETED_WITH_INVESTIGATION_FAILURES"
  | "NO_CASES_REQUIRED_INVESTIGATION"
  | "NOT_INVESTIGATED"
  | "UNKNOWN";

export interface RunInvestigationReport {
  status: InvestigationStatus;
  /** Short label for a badge. */
  label: string;
  /** One sentence a reviewer can act on. */
  detail: string;
  /** True when the run must not be presented as fully investigated. */
  warning: boolean;
  failureCount: number;
  investigatedCount: number;
  attemptedProviders: string[];
  actualProviders: string[];
}

function numberAt(source: Record<string, unknown>, key: string): number {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function stringsAt(source: Record<string, unknown>, key: string): string[] {
  const value = source[key];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

const LABELS: Record<InvestigationStatus, string> = {
  FULLY_INVESTIGATED: "Fully investigated",
  COMPLETED_WITH_INVESTIGATION_FAILURES: "Completed with investigation failures",
  NO_CASES_REQUIRED_INVESTIGATION: "No cases required investigation",
  NOT_INVESTIGATED: "Rules only, not investigated",
  UNKNOWN: "Investigation status unavailable",
};

/** Build the report from a run summary, tolerating an absent or partial one. */
export function describeRunInvestigation(
  summary: Record<string, unknown> | null,
): RunInvestigationReport {
  const investigation =
    summary && typeof summary.investigation === "object" && summary.investigation !== null
      ? (summary.investigation as Record<string, unknown>)
      : {};
  const attemptedProviders = stringsAt(investigation, "attempted_providers");
  const actualProviders = stringsAt(investigation, "actual_providers");
  const failureCount = summary
    ? numberAt(summary, "investigation_failure_count") ||
      numberAt(investigation, "investigation_failure_count")
    : 0;
  const investigatedCount = numberAt(investigation, "investigated_case_count");

  const raw = summary?.investigation_status;
  const status: InvestigationStatus =
    typeof raw === "string" && raw in LABELS ? (raw as InvestigationStatus) : "UNKNOWN";

  let detail: string;
  if (status === "COMPLETED_WITH_INVESTIGATION_FAILURES") {
    const plural = failureCount === 1 ? "case" : "cases";
    const noAnswer =
      attemptedProviders.length > 0 && actualProviders.length === 0
        ? ` No provider returned a completed model turn (attempted: ${attemptedProviders.join(", ")}).`
        : "";
    detail =
      `Reconciliation and deterministic verification completed. ` +
      `${failureCount} ${plural} could not be investigated and remain unresolved, ` +
      `with no proof, dry-run or approval path.${noAnswer}`;
  } else if (status === "FULLY_INVESTIGATED") {
    const who = actualProviders.length ? actualProviders.join(", ") : "the selected investigator";
    detail = `All ${investigatedCount} investigated case${
      investigatedCount === 1 ? "" : "s"
    } completed through ${who}. Deterministic verification remains authoritative.`;
  } else if (status === "NO_CASES_REQUIRED_INVESTIGATION") {
    detail =
      "Deterministic reconciliation resolved every record, so no case needed AI investigation.";
  } else if (status === "NOT_INVESTIGATED") {
    detail = "This run used deterministic rules only. No AI investigation was requested.";
  } else {
    detail = "This run did not report an investigation status.";
  }

  return {
    status,
    label: LABELS[status],
    detail,
    warning: status === "COMPLETED_WITH_INVESTIGATION_FAILURES" || status === "UNKNOWN",
    failureCount,
    investigatedCount,
    attemptedProviders,
    actualProviders,
  };
}
