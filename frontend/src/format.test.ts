import { describe, expect, it } from "vitest";
import {
  CATEGORY_COUNT,
  assignStoreHues,
  barAxisLabel,
  categoryIndex,
  storeVar,
  day,
  humanise,
  money,
  number,
  paidLabel,
  paidWord,
  percent,
  saving,
} from "./format";

describe("categoryIndex", () => {
  it("is stable for a key across calls", () => {
    // The whole point of hashing rather than assigning by position: a store
    // keeps its colour when the list it sits in is filtered or reordered.
    expect(categoryIndex("00318")).toBe(categoryIndex("00318"));
  });

  it("stays inside the palette", () => {
    for (const key of [
      "",
      "00318",
      "SIMPLE TRUTH ORG LEMON 6CT",
      "ééé",
      "0".repeat(500),
    ]) {
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

describe("saving", () => {
  it("tells silence apart from a disclosed zero", () => {
    // The distinction the whole em-dash decision turns on. Absence gets the
    // dash; a full-price line gets nothing, because it was disclosed and the
    // dash no longer means what it once did here.
    expect(saving(null)).toBe("—");
    expect(saving(undefined)).toBe("—");
    expect(saving(0)).toBe("");
  });

  it("renders a real saving as a leading minus, in ink", () => {
    // DESIGN.md retired green: the minus sign has carried this for centuries.
    expect(saving(1.5)).toContain("−");
    expect(saving(1.5)).toContain("1.50");
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

describe("paidWord and its two phrasings", () => {
  it("says paid when lines were disclosed, spent when they were not", () => {
    // "Paid" means a summed line amount everywhere in this app. A response
    // carrying only stated basket totals holds a different quantity, and the
    // word changes with the figure rather than covering both.
    expect(paidWord(true)).toBe("paid");
    expect(paidWord(false)).toBe("spent");
  });

  it("gives the headline and the axis the same word", () => {
    // Issue #57 was one figure under two names depending on the tab. The two
    // phrasings differ; the word they are built from must not.
    expect(paidLabel(true)).toBe("Total paid");
    expect(barAxisLabel(true)).toBe("paid, by month");
    expect(paidLabel(false)).toBe("Total spent");
    expect(barAxisLabel(false)).toBe("spent, by month");
  });

  it("derives both phrasings from the one word", () => {
    for (const disclosed of [true, false]) {
      expect(paidLabel(disclosed)).toContain(paidWord(disclosed));
      expect(barAxisLabel(disclosed)).toContain(paidWord(disclosed));
    }
  });
});

describe("assignStoreHues", () => {
  /** Two keys that hash to the same bucket. Found by search, not assumed —
   *  if the hash ever changes this fails loudly rather than testing nothing. */
  const collidingPair = (() => {
    const seen = new Map<number, string>();
    for (let i = 0; i < 20000; i++) {
      const key = `STORE-${i}`;
      const bucket = categoryIndex(key);
      const first = seen.get(bucket);
      if (first) return [first, key];
      seen.set(bucket, key);
    }
    throw new Error("no colliding pair found; the hash changed");
  })();

  it("leaves a set with no collisions exactly as the hash had it", () => {
    // The whole point of breaking ties rather than reassigning: a store that
    // was not part of the problem keeps the colour it has always had.
    const keys = ["00318", "00427"];
    const hues = assignStoreHues(keys);
    for (const key of keys) {
      expect(hues.get(key)).toBe(categoryIndex(key));
    }
  });

  it("separates two keys that the hash puts on the same hue", () => {
    const [a, b] = collidingPair;
    expect(categoryIndex(a)).toBe(categoryIndex(b));
    const hues = assignStoreHues([a, b]);
    expect(hues.get(a)).not.toBe(hues.get(b));
  });

  it("moves only the later key, and leaves the first one on its hash", () => {
    const [a, b] = collidingPair;
    const [first, second] = [a, b].sort();
    const hues = assignStoreHues([a, b]);
    expect(hues.get(first)).toBe(categoryIndex(first));
    expect(hues.get(second)).not.toBe(categoryIndex(second));
  });

  it("does not depend on the order the keys arrive in", () => {
    // The caller passes stats.stores, which is ordered by visit count. A store
    // gaining a visit must not repaint the legend.
    const [a, b] = collidingPair;
    const forward = assignStoreHues([a, b]);
    const backward = assignStoreHues([b, a]);
    expect([...forward.entries()].sort()).toEqual(
      [...backward.entries()].sort(),
    );
  });

  it("gives every key a hue inside the palette", () => {
    const keys = Array.from({ length: 10 }, (_, i) => `STORE-${i}`);
    for (const index of assignStoreHues(keys).values()) {
      expect(index).toBeGreaterThanOrEqual(0);
      expect(index).toBeLessThan(CATEGORY_COUNT);
    }
  });

  it("keeps the hash's answer once there is no free hue left", () => {
    // Past CATEGORY_COUNT there is nothing to move to. Collisions come back,
    // which is the behaviour this had before and is why DESIGN.md records the
    // ladder rather than claiming the palette scales.
    const keys = Array.from({ length: 12 }, (_, i) => `S${i}`);
    const hues = assignStoreHues(keys);
    expect(hues.size).toBe(12);
    const used = new Set(hues.values());
    expect(used.size).toBeLessThanOrEqual(CATEGORY_COUNT);
  });

  it("resolves a colliding pair into hues that are far apart, not merely different", () => {
    // The finding that made this worth doing: --cat-2 and --cat-4 are ΔE 1.1
    // apart under deuteranopia, so "pick any free index" can change nothing at
    // all for a colour-blind reader while this test passes on the numbers
    // differing. Both resolved hues must come from the front of the measured
    // spread order, which is where the separation is.
    const [a, b] = collidingPair;
    const hues = assignStoreHues([a, b]);
    const indistinguishable = [
      [1, 3], // cat-2 / cat-4
      [0, 4], // cat-1 / cat-5
      [1, 5], // cat-2 / cat-6
    ];
    const got = [hues.get(a)!, hues.get(b)!].sort();
    for (const pair of indistinguishable) {
      expect(got).not.toEqual(pair);
    }
  });

  it("falls back to the hash for a key it was never given", () => {
    // A basket can carry a store code that stats.stores excludes, because that
    // query drops nulls. Returning undefined would paint a transparent dot.
    const hues = assignStoreHues(["00318"]);
    expect(storeVar(hues, "00427")).toBe(
      `var(--cat-${categoryIndex("00427") + 1})`,
    );
    expect(storeVar(hues, "00318")).toBe(`var(--cat-${hues.get("00318")! + 1})`);
  });
});
