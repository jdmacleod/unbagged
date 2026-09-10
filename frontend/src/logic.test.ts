import { describe, expect, it } from "vitest";
import {
  currentMonthKey,
  doesNotFoot,
  fillMonths,
  monthIndex,
} from "./views/Timeline";
import { scale } from "./views/PriceHistory";
import { gaugeWidth, scopeNote } from "./views/Profile";
import { draftRows } from "./views/Compliance";
import { rowsFor } from "./views/Compare";
import { nothingWasItemised } from "./views/ProductIndex";
import { announceUpload, isSameEntry, resolveCurrent } from "./App";
import type { View } from "./App";
import type {
  Basket,
  CompareRow,
  Identity,
  Inference,
  PricePoint,
  UploadResult,
} from "./types";

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
  ({
    date,
    retail_amt: retail,
    paid_amt: paid,
    saved_amt: 0,
    multiple_of: null,
  }) as PricePoint;

describe("scale", () => {
  it("places points by date, not by position in the array", () => {
    // The reason Recharts was removed: its category axis drew irregular dates
    // at equal spacing, which on a time series is a correctness bug.
    const pts = [
      point("2024-01-01", 1),
      point("2024-01-02", 1),
      point("2024-12-31", 1),
    ];
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

/** An itemised visit: the retailer said what was in the basket. */
const visit = (occurred_at: string, paid = 10, saved = 0): Basket =>
  ({
    occurred_at,
    paid_total: paid,
    saved_total: saved,
    lines_disclosed: true,
  }) as Basket;

/** A visit the retailer priced but never itemised. */
const totalOnly = (occurred_at: string, stated = 10): Basket =>
  ({
    occurred_at,
    paid_total: null,
    saved_total: null,
    total_pre_discount: stated,
    lines_disclosed: false,
  }) as Basket;

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
      {
        occurred_at: "2024-02-20T10:00:00",
        paid_total: null,
        saved_total: null,
        lines_disclosed: true,
      } as unknown as Basket,
    ]);
    expect(months[0].paid).toBe(0);
    expect(months[0].saved).toBe(0);
  });

  it("draws an unitemised month from the stated totals", () => {
    // Every line-derived figure on these baskets is null. Coalescing those to
    // zero summed the month to nothing, the peak fell to its floor, and every
    // bar rendered at 0px under a heading that still said "paid".
    const months = monthIndex([
      totalOnly("2024-02-20T10:00:00", 12.5),
      totalOnly("2024-02-27T10:00:00", 7.5),
    ]);
    expect(months[0].paid).toBe(20);
    expect(months[0].unitemised).toBe(2);
    expect(months[0].itemised).toBe(0);
  });

  it("never adds a stated total to a summed one in a mixed month", () => {
    // Two different quantities under one mark, with no note, is the failure
    // this rule exists to prevent. The bar draws from what was itemised and
    // says how many visits it left out.
    const months = monthIndex([
      visit("2024-02-20T10:00:00", 10, 0),
      totalOnly("2024-02-27T10:00:00", 99),
    ]);
    expect(months[0].paid).toBe(10);
    expect(months[0].stated).toBe(99);
    expect(months[0].itemised).toBe(1);
    expect(months[0].unitemised).toBe(1);
  });
});

describe("fillMonths", () => {
  it("puts the empty months back", () => {
    // monthIndex emits only months that have a basket, so a gap where you
    // stopped shopping rendered as no gap at all: the bars ran continuously
    // and the months in between did not exist.
    const filled = fillMonths(
      monthIndex([visit("2024-01-10T10:00:00"), visit("2024-05-10T10:00:00")]),
    );
    expect(filled.map((m) => m.key)).toEqual([
      "2024-01",
      "2024-02",
      "2024-03",
      "2024-04",
      "2024-05",
    ]);
    expect(filled.map((m) => m.visits)).toEqual([1, 0, 0, 0, 1]);
  });

  it("crosses a year boundary", () => {
    const filled = fillMonths(
      monthIndex([visit("2023-11-10T10:00:00"), visit("2024-02-10T10:00:00")]),
    );
    expect(filled.map((m) => m.key)).toEqual([
      "2023-11",
      "2023-12",
      "2024-01",
      "2024-02",
    ]);
  });

  it("is a no-op on an empty roll", () => {
    expect(fillMonths([])).toEqual([]);
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

describe("scopeNote", () => {
  const id = (scope: string | null) => ({ scope }) as unknown as Identity;

  it("says nothing when there are no identifiers to characterise", () => {
    // Both of the counting branches read 0 === 0 as true on an empty list, so
    // this used to print a claim about scope in the margin beside a section
    // saying no identifiers were found. Reachable, because the whole-view
    // empty state also needs the inferences to be empty.
    expect(scopeNote([])).toBe("");
  });

  it("does not claim individual when the response never said", () => {
    // An adapter that records scope as null rather than inventing one is
    // making the honest choice; the margin must not undo it.
    expect(scopeNote([id(null)])).toBe("scope not stated");
  });

  it("names a household scope, which covers people who never enrolled", () => {
    expect(scopeNote([id("household"), id("individual")])).toBe(
      "1 household-scoped",
    );
  });

  it("reports a mixture rather than rounding it to one answer", () => {
    expect(scopeNote([id("individual"), id(null)])).toBe(
      "1 with no scope stated",
    );
  });

  it("says all individual only when every one of them says so", () => {
    expect(scopeNote([id("individual"), id("individual")])).toBe(
      "all individual",
    );
  });
});

describe("nothingWasItemised", () => {
  // Regression: ISSUE-004 — the index was gated on `disclosed`, which a
  // response carrying a total for every visit and no line items satisfies.
  // Found by /qa on 2026-09-09.
  // Report: .gstack/qa-reports/qa-report-hmart-2026-09-09.md
  const index = (total_products: number, lines_disclosed: boolean) => ({
    total_products,
    lines_disclosed,
  });

  it("is true for a response that priced its visits and itemised none", () => {
    expect(nothingWasItemised(index(0, false))).toBe(true);
  });

  it("is false once anything was itemised", () => {
    expect(nothingWasItemised(index(399, true))).toBe(false);
  });

  it("does not distinguish a refusal, which is why order matters", () => {
    // A response that disclosed nothing satisfies this as well, and needs the
    // other message: it disclosed no visits either, so "they priced every visit
    // and itemised none" would be false. ProductIndex checks `!disclosed`
    // first. This pins the overlap so the ordering is not tidied away.
    expect(nothingWasItemised(index(0, false))).toBe(true);
  });

  it("is false for an itemised response whose index is empty", () => {
    // Reachable and different: lines were disclosed and none named a product.
    // "This response disclosed no products" is true there; "they never said
    // what was in any basket" is not.
    expect(nothingWasItemised(index(0, true))).toBe(false);
  });
});

describe("resolveCurrent", () => {
  it("shows what the URL asked for when the list has it", () => {
    expect(resolveCurrent([1, 2, 3], 2, null)).toBe(2);
  });

  it("falls back to the loaded list rather than to nothing", () => {
    // A bookmarked ?r= outlives its response — `make reset` is the obvious
    // way. Showing something beats "No request with id 999" beside a selector
    // confidently displaying a different one.
    expect(resolveCurrent([1, 2, 3], 999, null)).toBe(1);
  });

  it("trusts a response an upload just created, before the list catches up", () => {
    // The whole point. `reload` retains the old rows, so for at least one
    // render the new id is unrecognised — and the fallback is `rows[0]`, the
    // OLDEST response, since list_requests is ORDER BY id. Without this the
    // reader gets a flash of the wrong retailer and a round of view fetches
    // against the wrong request id.
    expect(resolveCurrent([1, 2], 3, 3)).toBe(3);
  });

  it("does not extend that trust to some other unknown id", () => {
    // Only the row the 201 just returned gets the benefit of the doubt.
    expect(resolveCurrent([1, 2], 9, 3)).toBe(1);
  });

  it("leaves the first run alone, where there is no wrong row to pick", () => {
    // With an empty list the fallback cannot choose badly, and selecting the
    // pending row would render an empty <main> above the first-run uploader
    // for the length of the reload.
    expect(resolveCurrent([], 1, 1)).toBe(null);
  });

  it("takes the first response when the URL names none", () => {
    // A fresh session with no `?r=`. Reached in the browser tier only, so it is
    // worth a line here where it is one.
    expect(resolveCurrent([4, 7, 9], null, null)).toBe(4);
  });

  it("is null when there is nothing at all", () => {
    expect(resolveCurrent([], null, null)).toBe(null);
  });
});

const view = (v: Partial<View> = {}): View => ({
  tab: "timeline",
  request: 1,
  query: null,
  label: null,
  ...v,
});

describe("isSameEntry", () => {
  it("recognises a navigation that lands where it started", () => {
    // Clicking the tab you are already on. Pushing here is a Back press that
    // appears to do nothing, which teaches people the button is broken.
    expect(isSameEntry(view(), view())).toBe(true);
  });

  it("separates entries by tab", () => {
    expect(isSameEntry(view({ tab: "profile" }), view())).toBe(false);
  });

  it("separates entries by response", () => {
    expect(isSameEntry(view({ request: 2 }), view())).toBe(false);
  });

  it("counts the product filter, which is a different reading of one tab", () => {
    // ?q= silently filters the timeline. Arriving at a filtered view and
    // leaving it are two things Back should walk between.
    expect(isSameEntry(view({ query: "0001" }), view())).toBe(false);
  });

  it("counts the label, because it is what the arrival sentence says", () => {
    expect(
      isSameEntry(
        view({ query: "0001", label: "BANANAS EA" }),
        view({ query: "0001", label: "MILK 2%" }),
      ),
    ).toBe(false);
  });
});

const uploaded = (over: Partial<UploadResult> = {}): UploadResult =>
  ({
    request_id: 1,
    retailer_id: "kroger",
    display_name: "Kroger",
    confident: true,
    confidence: 0.98,
    warnings: [],
    summary: { transactions: 54, items: 1203, identities: 7, inferences: 12 },
    ...over,
  }) as UploadResult;

describe("announceUpload", () => {
  it("says what was read and how much of it", () => {
    // Nothing moved focus or announced anything when <main> was replaced, so
    // the upload a screen reader user started produced silence.
    const said = announceUpload(uploaded());
    expect(said).toContain("Read as Kroger.");
    // 54 has no group separator in any locale; 1203 does, and which one depends
    // on the host — `toLocaleString()` takes no locale here, so this would be
    // "1.203" under de-DE and "1 203" under fr-FR. Assert the shape, not the
    // separator, or this fails on a machine whose LANG happens to differ.
    expect(said).toContain("54 visits");
    expect(said).toMatch(/[\d.,\u202f\u00a0]+ line items/);
    expect(said).toContain("identifiers");
  });

  it("carries the uncertainty, which is the part worth hearing", () => {
    // A low-confidence match is the one case where the retailer named may not
    // be the retailer meant, and it is invisible to someone not looking at the
    // panel.
    expect(announceUpload(uploaded({ confident: false }))).toContain(
      "uncertain match",
    );
  });

  it("counts warnings rather than reading all of them out", () => {
    const said = announceUpload(
      uploaded({
        warnings: [
          { message: "a", locator: null },
          { message: "b", locator: null },
        ],
      } as Partial<UploadResult>),
    );
    expect(said).toContain("2 warnings.");
  });

  it("says one warning in the singular", () => {
    // The boundary the conditional exists for, and the commonest real case:
    // the generic adapter's letter produces exactly one. Untested, "1 warnings."
    // would have shipped green.
    expect(
      announceUpload(
        uploaded({
          warnings: [{ message: "a", locator: null }],
        } as Partial<UploadResult>),
      ),
    ).toContain("1 warning.");
  });

  it("says nothing about warnings when there are none", () => {
    expect(announceUpload(uploaded())).not.toContain("warning");
  });
});

describe("rowsFor", () => {
  const column = (
    disclosed: boolean,
    lines_disclosed: boolean,
  ): CompareRow =>
    ({ id: 1, disclosed, lines_disclosed }) as CompareRow;

  const moneyLabel = (requests: CompareRow[]) =>
    rowsFor(requests).find((r) => r.key === "total_paid")!.label;

  it("says paid when every disclosed column has line items", () => {
    expect(moneyLabel([column(true, true), column(true, true)])).toBe(
      "Total paid",
    );
  });

  it("says spent when any disclosed column has none", () => {
    // The H Mart case beside a Kroger one. "Paid" would claim a summed line
    // amount for a column that only ever carried stated basket totals.
    expect(moneyLabel([column(true, true), column(true, false)])).toBe(
      "Total spent",
    );
  });

  it("ignores columns that disclosed nothing at all", () => {
    // Every cell in an undisclosed column is an em dash, so it has no quantity
    // to name and must not drag the word for the columns that do.
    expect(moneyLabel([column(true, true), column(false, false)])).toBe(
      "Total paid",
    );
  });

  it("falls back to paid when no column is disclosed", () => {
    // `every` over an empty list is true. Called out because it looks like a
    // bug and is not: the row is all em dashes here, and "Total paid" is what
    // this label said before any of it varied.
    expect(moneyLabel([column(false, false)])).toBe("Total paid");
    expect(moneyLabel([])).toBe("Total paid");
  });

  it("changes only the money label, never the row set or the keys", () => {
    const paid = rowsFor([column(true, true)]);
    const spent = rowsFor([column(true, false)]);
    expect(paid.map((r) => r.key)).toEqual(spent.map((r) => r.key));
    const differing = paid.filter((r, i) => r.label !== spent[i].label);
    expect(differing.map((r) => r.key)).toEqual(["total_paid"]);
  });
});
