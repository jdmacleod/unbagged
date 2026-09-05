import { describe, expect, it } from "vitest";
import { currentMonthKey, doesNotFoot, monthIndex } from "./views/Timeline";
import { scale } from "./views/PriceHistory";
import { gaugeWidth } from "./views/Profile";
import { draftRows } from "./views/Compliance";
import type { Basket, Inference, PricePoint } from "./types";

const basket = (delta: number | null): Basket =>
  ({ stated_pre_discount_delta: delta }) as Basket;

describe("doesNotFoot", () => {
  it("ignores float noise from summing currency", () => {
    // A cent. Below that a difference is the residue of adding decimals in
    // binary, not a disagreement in the response.
    expect(doesNotFoot(basket(0.004))).toBe(false);
    expect(doesNotFoot(basket(-0.004))).toBe(false);
  });

  it("marks a real gap in either direction", () => {
    // Both signs are real and mean opposite things: over means the lines
    // exceed the stated total, under means they fall short.
    expect(doesNotFoot(basket(0.01))).toBe(true);
    expect(doesNotFoot(basket(-8.14))).toBe(true);
  });

  it("is silent when the retailer stated no total to check against", () => {
    // Null is "nothing to compare", not "it balanced".
    expect(doesNotFoot(basket(null))).toBe(false);
  });
});

const point = (date: string, retail: number, paid = retail): PricePoint =>
  ({ date, retail_amt: retail, paid_amt: paid, saved_amt: 0, multiple_of: null }) as PricePoint;

describe("scale", () => {
  it("places points by date, not by position in the array", () => {
    // The reason Recharts was removed: its category axis drew irregular dates
    // at equal spacing, which on a time series is a correctness bug.
    const pts = [point("2024-01-01", 1), point("2024-01-02", 1), point("2024-12-31", 1)];
    const s = scale(pts);
    const [a, b, c] = pts.map(s.x);
    expect(b - a).toBeLessThan((c - b) / 10);
  });

  it("survives every point sharing one date", () => {
    // A zero time span would divide by zero and put every x at NaN.
    const s = scale([point("2024-05-04", 2), point("2024-05-04", 3)]);
    expect(Number.isFinite(s.x(point("2024-05-04", 2)))).toBe(true);
  });

  it("survives every amount being identical", () => {
    // Same hazard on the other axis: a flat series has no range to normalise
    // against, and a product bought repeatedly at one price is common.
    const s = scale([point("2024-01-01", 3.11), point("2024-06-01", 3.11)]);
    expect(Number.isFinite(s.y(3.11))).toBe(true);
    expect(s.lo).toBe(3.11);
    expect(s.hi).toBe(3.11);
  });

  it("leaves the extremes off the edge", () => {
    // Headroom, so the highest point is not welded to the top of the box.
    const s = scale([point("2024-01-01", 1), point("2024-06-01", 5)]);
    expect(s.y(5)).toBeGreaterThan(0);
    expect(s.y(1)).toBeLessThan(116);
  });
});

const inference = (scaleText: string | null, value: number | null): Inference =>
  ({ scale: scaleText, value_num: value }) as Inference;

describe("gaugeWidth", () => {
  it("draws nothing without both a number and a scale", () => {
    expect(gaugeWidth(inference(null, 4))).toBeNull();
    expect(gaugeWidth(inference("ordinal_1_7", null))).toBeNull();
  });

  it("reads a range out of the scale label", () => {
    expect(gaugeWidth(inference("Ordinal 1–7", 1))).toBe(0);
    expect(gaugeWidth(inference("Ordinal 1–7", 7))).toBe(100);
    expect(gaugeWidth(inference("Ordinal 1–7", 4))).toBe(50);
  });

  it("clamps a value outside its own stated scale", () => {
    // The scale is the retailer's claim; the value is too. They need not agree,
    // and a bar drawn at -50% or 300% would overflow its track.
    expect(gaugeWidth(inference("Ordinal 1–7", 99))).toBe(100);
    expect(gaugeWidth(inference("Ordinal 1–7", -5))).toBe(0);
  });

  it("refuses a degenerate range rather than dividing by zero", () => {
    expect(gaugeWidth(inference("Ordinal 3–3", 3))).toBeNull();
  });
});

const visit = (occurred_at: string, paid = 10, saved = 0): Basket =>
  ({ occurred_at, paid_total: paid, saved_total: saved }) as Basket;

describe("monthIndex", () => {
  it("groups the roll into months in the order the rows appear", () => {
    const months = monthIndex([
      visit("2024-02-20T10:00:00"),
      visit("2024-02-27T10:00:00"),
      visit("2024-03-07T10:00:00"),
    ]);
    expect(months.map((m) => m.key)).toEqual(["2024-02", "2024-03"]);
    expect(months.map((m) => m.visits)).toEqual([2, 1]);
  });

  it("records where each month starts in the unsliced list", () => {
    // This is what the rail reveals through before scrolling. Off by one here
    // and a jump lands on the last row of the previous month.
    const months = monthIndex([
      visit("2024-02-20T10:00:00"),
      visit("2024-02-27T10:00:00"),
      visit("2024-03-07T10:00:00"),
      visit("2024-04-01T10:00:00"),
    ]);
    expect(months.map((m) => m.firstIndex)).toEqual([0, 2, 3]);
  });

  it("sums paid and saved separately, so the bar can show both", () => {
    const months = monthIndex([
      visit("2024-02-20T10:00:00", 10, 2),
      visit("2024-02-27T10:00:00", 5, 1),
    ]);
    expect(months[0].paid).toBe(15);
    expect(months[0].saved).toBe(3);
  });

  it("treats a missing amount as zero rather than NaN", () => {
    // A bar of width NaN renders as no bar at all, silently.
    const months = monthIndex([
      { occurred_at: "2024-02-20T10:00:00", paid_total: null, saved_total: null } as
        unknown as Basket,
    ]);
    expect(months[0].paid).toBe(0);
    expect(months[0].saved).toBe(0);
  });

  it("labels the month the way the roll prints it", () => {
    expect(monthIndex([visit("2024-02-20T10:00:00")])[0].label).toBe("Feb 24");
  });

  it("returns nothing for an empty roll", () => {
    expect(monthIndex([])).toEqual([]);
  });
});

describe("currentMonthKey", () => {
  const marks = (...tops: number[]) =>
    tops.map((top, i) => ({ key: `m${i}`, top }));

  it("names the last month whose mark has passed the threshold", () => {
    // Not the first one still on screen. A month taller than the viewport has
    // no mark on screen at all, and the head would go blank in the middle of
    // the month it is supposed to be naming.
    expect(currentMonthKey(marks(-800, -400, 900))).toBe("m1");
  });

  it("is null above the first month, where there is no month yet", () => {
    expect(currentMonthKey(marks(300, 900))).toBe(null);
  });

  it("counts a mark exactly on the threshold as passed", () => {
    expect(currentMonthKey(marks(72))).toBe("m0");
    expect(currentMonthKey(marks(73))).toBe(null);
  });

  it("holds the last month once every mark is above the fold", () => {
    expect(currentMonthKey(marks(-2000, -1200, -300))).toBe("m2");
  });

  it("is null when nothing is rendered", () => {
    expect(currentMonthKey([])).toBe(null);
  });
});

describe("draftRows", () => {
  it("gives a short draft only the height it needs", () => {
    // The old fixed rows={18} padded a four-line follow-up with empty box.
    expect(draftRows("a\nb\nc\nd")).toBe(4);
  });

  it("caps a long draft rather than spending the whole section on it", () => {
    expect(draftRows(Array(40).fill("line").join("\n"))).toBe(8);
  });

  it("keeps a floor, so the field still reads as a document", () => {
    expect(draftRows("one line")).toBe(3);
    expect(draftRows("")).toBe(3);
  });

  it("takes a different cap when the reader asks for the whole thing", () => {
    const long = Array(40).fill("line").join("\n");
    expect(draftRows(long, 40)).toBe(40);
    // And still does not invent height the draft does not have.
    expect(draftRows("a\nb", 40)).toBe(3);
  });
});
