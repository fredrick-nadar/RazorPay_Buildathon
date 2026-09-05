/**
 * Display-only formatting helpers.
 *
 * Money arrives from the API as signed integer paise. Rendering uses exact
 * integer math on the paise value; no float arithmetic participates in the
 * displayed figures.
 */

export function formatINR(paise: number): string {
  const negative = paise < 0;
  const abs = Math.abs(paise);
  const rupees = Math.floor(abs / 100);
  const paisa = abs % 100;
  return `${negative ? "\u2212" : ""}\u20B9${rupees.toLocaleString("en-IN")}.${paisa
    .toString()
    .padStart(2, "0")}`;
}

/**
 * Render integer basis points as a percentage using exact integer arithmetic.
 *
 * 200 bps -> "2.00%", 1800 bps -> "18.00%", 12345 bps -> "123.45%". Policy
 * rates arrive as integer bps precisely so they never pass through a float;
 * this keeps the displayed rate on the same footing.
 */
export function formatBps(bps: number): string {
  if (!Number.isInteger(bps)) return "—";
  const negative = bps < 0;
  const abs = Math.abs(bps);
  const whole = Math.floor(abs / 100);
  const fraction = abs % 100;
  return `${negative ? "−" : ""}${whole}.${fraction.toString().padStart(2, "0")}%`;
}

/** Signed paise with an explicit sign, for a delta the reader must not misread. */
export function formatSignedINR(paise: number): string {
  if (!Number.isFinite(paise)) return "—";
  if (paise === 0) return formatINR(0);
  const rendered = formatINR(Math.abs(paise));
  return paise < 0 ? `−${rendered}` : `+${rendered}`;
}

export function formatRate(numerator: number, denominator: number): string {
  if (!Number.isFinite(numerator) || !denominator || denominator <= 0) return "\u2014";
  return `${((numerator / denominator) * 100).toFixed(1)}%`;
}

export function formatCount(value: number | undefined | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "\u2014";
  return value.toLocaleString("en-IN");
}

/** Compact UTC timestamp: 2026-08-24T10:12:33.456789+00:00 → 2026-08-24 10:12:33Z */
export function formatUtc(iso: string | null | undefined): string {
  if (!iso) return "\u2014";
  const cut = iso.indexOf(".");
  const base = cut > 0 ? iso.slice(0, cut) : iso.replace(/([+-]\d{2}:\d{2}|Z)$/, "");
  return base.replace("T", " ") + "Z";
}

export function shortHash(hash: string | null | undefined, keep = 14): string {
  if (!hash) return "";
  return hash.length <= keep ? hash : `${hash.slice(0, keep)}\u2026`;
}

export function humanizeEnum(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
