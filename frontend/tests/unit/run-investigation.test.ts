import { describe, expect, it } from "vitest";
import { describeRunInvestigation } from "../../src/lib/run-investigation";

/**
 * A completed financial run with failed investigations must never read as a
 * fully investigated run, and the two provider sets must stay distinct.
 */

const FAILED_RUN = {
  batch_status: "COMPLETED",
  investigation_status: "COMPLETED_WITH_INVESTIGATION_FAILURES",
  investigation_failure_count: 4,
  investigation: {
    investigated_case_count: 4,
    investigation_failure_count: 4,
    fully_investigated: false,
    // The live-failure shape: dialled, but nobody answered.
    attempted_providers: ["groq", "sarvam"],
    actual_providers: [],
  },
};

describe("describeRunInvestigation", () => {
  it("warns and counts when investigations failed", () => {
    const report = describeRunInvestigation(FAILED_RUN);
    expect(report.status).toBe("COMPLETED_WITH_INVESTIGATION_FAILURES");
    expect(report.label).toBe("Completed with investigation failures");
    expect(report.warning).toBe(true);
    expect(report.failureCount).toBe(4);
    expect(report.detail).toContain("remain unresolved");
    expect(report.detail).toContain("no proof, dry-run or approval path");
  });

  it("says plainly when no provider returned a completed turn", () => {
    const report = describeRunInvestigation(FAILED_RUN);
    expect(report.attemptedProviders).toEqual(["groq", "sarvam"]);
    expect(report.actualProviders).toEqual([]);
    expect(report.detail).toContain("No provider returned a completed model turn");
    expect(report.detail).toContain("groq, sarvam");
  });

  it("does not claim a missing answer when a provider did answer", () => {
    const report = describeRunInvestigation({
      ...FAILED_RUN,
      investigation: {
        ...FAILED_RUN.investigation,
        attempted_providers: ["groq", "sarvam"],
        actual_providers: ["sarvam"],
      },
    });
    expect(report.warning).toBe(true);
    expect(report.detail).not.toContain("No provider returned");
  });

  it("reports a fully investigated run without a warning", () => {
    const report = describeRunInvestigation({
      investigation_status: "FULLY_INVESTIGATED",
      investigation_failure_count: 0,
      investigation: {
        investigated_case_count: 3,
        investigation_failure_count: 0,
        fully_investigated: true,
        attempted_providers: ["groq"],
        actual_providers: ["groq"],
      },
    });
    expect(report.warning).toBe(false);
    expect(report.label).toBe("Fully investigated");
    expect(report.detail).toContain("groq");
    expect(report.detail).toContain("Deterministic verification remains authoritative");
  });

  it("distinguishes a clean run from a rules-only run", () => {
    expect(
      describeRunInvestigation({
        investigation_status: "NO_CASES_REQUIRED_INVESTIGATION",
        investigation: {},
      }).warning,
    ).toBe(false);
    const rules = describeRunInvestigation({
      investigation_status: "NOT_INVESTIGATED",
    });
    expect(rules.label).toBe("Rules only, not investigated");
    expect(rules.warning).toBe(false);
  });

  it("treats a missing status as unknown and warns rather than assuming success", () => {
    for (const summary of [null, {}, { investigation_status: "SOMETHING_ELSE" }]) {
      const report = describeRunInvestigation(summary);
      expect(report.status).toBe("UNKNOWN");
      expect(report.warning).toBe(true);
    }
  });

  it("tolerates a malformed summary without throwing", () => {
    const report = describeRunInvestigation({
      investigation_status: "COMPLETED_WITH_INVESTIGATION_FAILURES",
      investigation_failure_count: "four",
      investigation: { attempted_providers: ["groq", 7], actual_providers: "sarvam" },
    } as unknown as Record<string, unknown>);
    expect(report.attemptedProviders).toEqual(["groq"]);
    expect(report.actualProviders).toEqual([]);
    expect(report.failureCount).toBe(0);
  });

  it("uses singular wording for a single failed case", () => {
    const report = describeRunInvestigation({
      investigation_status: "COMPLETED_WITH_INVESTIGATION_FAILURES",
      investigation_failure_count: 1,
      investigation: { investigated_case_count: 1, attempted_providers: [], actual_providers: [] },
    });
    expect(report.detail).toContain("1 case could not be investigated");
  });
});
