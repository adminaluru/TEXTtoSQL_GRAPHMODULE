// Display helpers. They format what the server returned and never calculate.
// Amounts arrive as exact decimal text ("4000.00"); they are grouped as text,
// never converted to floating point, so no cent can be lost or invented.

import type { CellValue } from "../api/types";

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

/** "24000.5000" -> "24,000.50". Removes trailing zeros only (never rounds). */
export function formatNumberText(text: string): string {
  const match = DECIMAL.exec(text.trim());
  if (!match) return text;
  const [, sign, whole, fraction] = match;
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  if (fraction === undefined) return sign + grouped;
  let kept = fraction.replace(/0+$/, "");
  if (kept.length < 2) kept = kept.padEnd(2, "0");
  return `${sign}${grouped}.${kept}`;
}

export function formatValue(value: CellValue): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return formatNumberText(String(value));
  return DECIMAL.test(value.trim()) ? formatNumberText(value) : value;
}

/** "24,000.00 USD"; an unknown currency is said, not guessed. */
export function formatAmount(value: CellValue, currency: string | null | undefined): string {
  const amount = formatValue(value);
  if (currency === undefined) return amount;
  return `${amount} ${currency ? currency : "(currency unknown)"}`;
}

/** "sum_payment_amount" -> "Sum payment amount". */
export function humanize(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function isNumeric(value: CellValue): boolean {
  return typeof value === "number" || (typeof value === "string" && DECIMAL.test(value.trim()));
}
