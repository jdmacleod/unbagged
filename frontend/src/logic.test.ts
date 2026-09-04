import { describe, expect, it } from "vitest";
import { doesNotFoot } from "./views/Timeline";
import { scale } from "./views/PriceHistory";
import { gaugeWidth } from "./views/Profile";
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
