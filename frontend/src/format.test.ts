import { describe, expect, it } from "vitest";
import { CATEGORY_COUNT, categoryIndex, day, humanise, money, number, percent } from "./format";

describe("categoryIndex", () => {
  it("is stable for a key across calls", () => {
    // The whole point of hashing rather than assigning by position: a store
    // keeps its colour when the list it sits in is filtered or reordered.
    expect(categoryIndex("00318")).toBe(categoryIndex("00318"));
  });

  it("stays inside the palette", () => {
    for (const key of ["", "00318", "SIMPLE TRUTH ORG LEMON 6CT", "ééé", "0".repeat(500)]) {
      const i = categoryIndex(key);
      expect(i).toBeGreaterThanOrEqual(0);
      expect(i).toBeLessThan(CATEGORY_COUNT);
    }
  });

  it("never returns a negative index, even when the hash overflows", () => {
    // The hash is `(hash * 31 + code) | 0`, which wraps into negatives on a
    // long key. Math.abs is what keeps the index in range, and a long product
    // name is exactly the case that reaches it.
    expect(categoryIndex("Z".repeat(64))).toBeGreaterThanOrEqual(0);
  });

  it("spreads keys across more than one hue", () => {
    const seen = new Set(
      ["00318", "00427", "00891", "01102", "02201", "03310"].map(categoryIndex),
    );
    expect(seen.size).toBeGreaterThan(1);
  });
});

describe("money and number", () => {
  it("renders an em dash for absent, not zero", () => {
    // A dash means "not disclosed". Rendering 0.00 would state a fact the
    // response never gave.
    expect(money(null)).toBe("—");
    expect(money(undefined)).toBe("—");
    expect(number(null)).toBe("—");
    expect(money(0)).not.toBe("—");
  });
});

describe("percent", () => {
  it("signs a rise and leaves a fall alone", () => {
    expect(percent(8.7)).toBe("+8.7%");
    expect(percent(-11.8)).toBe("-11.8%");
    expect(percent(0)).toBe("0.0%");
    expect(percent(null)).toBe("—");
  });
});

describe("day", () => {
  it("slices rather than parses", () => {
    // Parsing as UTC would move an evening shop to the next day. The report
    // gives a store-local wall clock with no timezone.
    expect(day("2024-02-03T22:40:00")).toBe("2024-02-03");
    expect(day(null)).toBe("—");
  });
});

describe("humanise", () => {
  it("reads a digit-underscore-digit run as a range, not a word break", () => {
    expect(humanise("ordinal_1_7")).toBe("Ordinal 1–7");
  });
  it("splits camelCase", () => {
    expect(humanise("householdComposition")).toBe("Household composition");
  });
});
