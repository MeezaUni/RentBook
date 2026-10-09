import { addMonths, formatINR, formatPeriod, paiseToInput, parseRupees } from "./money";

describe("money helpers", () => {
  it("formats paise with Indian digit grouping", () => {
    expect(formatINR(0)).toBe("₹0");
    expect(formatINR(1_200_000)).toBe("₹12,000");
    expect(formatINR(12_345_678_90)).toBe("₹1,23,45,678.90");
    expect(formatINR(-50_050)).toBe("-₹500.50");
    expect(formatINR(null)).toBe("—");
  });

  it("parses typed rupee amounts exactly, without floating point", () => {
    expect(parseRupees("12,000")).toBe(1_200_000);
    expect(parseRupees("₹ 1,00,000.5")).toBe(10_000_050);
    expect(parseRupees("0.07")).toBe(7);
    expect(parseRupees("19.99")).toBe(1999); // 19.99 * 100 is 1998.9999… in floating point
    expect(parseRupees("12.345")).toBeNull();
    expect(parseRupees("abc")).toBeNull();
    expect(parseRupees("-5")).toBeNull();
  });

  it("round-trips paise through the input format", () => {
    for (const p of [0, 1, 99, 100, 1_234_567]) expect(parseRupees(paiseToInput(p))).toBe(p);
  });

  it("handles month arithmetic across years", () => {
    expect(addMonths("2025-12", 1)).toBe("2026-01");
    expect(addMonths("2026-01", -1)).toBe("2025-12");
    expect(addMonths("2026-03", -15)).toBe("2024-12");
    expect(formatPeriod("2026-10")).toBe("October 2026");
  });
});
