/**
 * Display formatters must stay exact.
 *
 * Money reaches the UI as signed integer paise. `formatBps` and
 * `formatSignedINR` were added in Chunk 3C because the MDR/GST card rendered
 * money with `paise / 100` + `toFixed(2)` binary float arithmetic and printed
 * every negative variance as the literal string for zero rupees.
 */

import { describe, expect, it } from "vitest";

import { formatBps, formatINR, formatRate, formatSignedINR } from "../../src/lib/format";

describe("formatINR", () => {
  it("renders exact paise with Indian grouping", () => {
    expect(formatINR(0)).toBe("₹0.00");
    expect(formatINR(1)).toBe("₹0.01");
    expect(formatINR(2116738)).toBe("₹21,167.38");
    expect(formatINR(-2116738)).toBe("−₹21,167.38");
  });

  it("keeps a value that a float round-trip would lose", () => {
    // 8_675_309 / 100 is not exactly representable; integer division is.
    expect(formatINR(8675309)).toBe("₹86,753.09");
    expect(formatINR(70)).toBe("₹0.70");
    expect(formatINR(1_00_00_00_000)).toBe("₹1,00,00,000.00");
  });
});

describe("formatSignedINR", () => {
  it("always shows the direction of a nonzero delta", () => {
    expect(formatSignedINR(2116738)).toBe("+₹21,167.38");
    expect(formatSignedINR(-2116738)).toBe("−₹21,167.38");
  });

  it("shows zero without a sign", () => {
    expect(formatSignedINR(0)).toBe("₹0.00");
  });

  it("never collapses a small negative delta to zero", () => {
    // The old card rendered any variance <= 0 as "₹0.00".
    expect(formatSignedINR(-1)).toBe("−₹0.01");
    expect(formatSignedINR(-50)).toBe("−₹0.50");
  });

  it("refuses a non-finite value rather than printing NaN", () => {
    expect(formatSignedINR(Number.NaN)).toBe("—");
    expect(formatSignedINR(Number.POSITIVE_INFINITY)).toBe("—");
  });
});

describe("formatBps", () => {
  it("renders policy rates exactly from integer basis points", () => {
    expect(formatBps(200)).toBe("2.00%");
    expect(formatBps(1800)).toBe("18.00%");
    expect(formatBps(0)).toBe("0.00%");
    expect(formatBps(1)).toBe("0.01%");
    expect(formatBps(12345)).toBe("123.45%");
    expect(formatBps(-150)).toBe("−1.50%");
  });

  it("refuses a fractional basis point", () => {
    // Basis points are integers by contract; a fraction means a bad response.
    expect(formatBps(200.5)).toBe("—");
    expect(formatBps(Number.NaN)).toBe("—");
  });
});

describe("formatRate", () => {
  it("returns an em dash rather than a fabricated rate", () => {
    expect(formatRate(Number.NaN, 100)).toBe("—");
    expect(formatRate(10, 0)).toBe("—");
    expect(formatRate(10, -5)).toBe("—");
  });

  it("reports a real ratio to one decimal place", () => {
    expect(formatRate(273, 282)).toBe("96.8%");
    expect(formatRate(282, 282)).toBe("100.0%");
    expect(formatRate(0, 282)).toBe("0.0%");
  });
});
