import { describe, expect, it } from "vitest";

import { formatAmount, formatNumberText, formatValue, humanize } from "../format/values";

describe("number display (never calculates, never rounds)", () => {
  it.each([
    ["4000.00", "4,000.00"],
    ["24000", "24,000"],
    ["500.0000000000000000", "500.00"],
    ["0.125", "0.125"],
    ["-1234567.5", "-1,234,567.50"],
    ["12345678901234567890.12", "12,345,678,901,234,567,890.12"],
  ])("%s -> %s", (input, expected) => {
    expect(formatNumberText(input)).toBe(expected);
  });

  it("keeps non-numeric text and empty values honest", () => {
    expect(formatValue("OPEN")).toBe("OPEN");
    expect(formatValue(null)).toBe("—");
    expect(formatValue(18)).toBe("18");
  });

  it("always names the currency, and says when it is unknown", () => {
    expect(formatAmount("24000.00", "USD")).toBe("24,000.00 USD");
    expect(formatAmount("10.00", null)).toBe("10.00 (currency unknown)");
    expect(formatAmount(18, undefined)).toBe("18");
  });

  it("humanizes column keys", () => {
    expect(humanize("sum_payment_amount")).toBe("Sum payment amount");
  });
});
