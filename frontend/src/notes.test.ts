import { describe, expect, it } from "vitest";
import { answeredInPart } from "./views/Compliance";
import { truncationNote } from "./views/ProductIndex";

// Found by /devex-review on 2026-10-22, against a response large enough to be
// capped and one that answered two categories, both only in part.

describe("answeredInPart", () => {
  it("adds nothing when every answer was whole", () => {
    expect(answeredInPart(3, 0)).toBe("");
  });

  it("says how many of the answers were partial", () => {
    expect(answeredInPart(3, 1)).toBe(", 1 of them only in part");
  });

  it("says when every answer was partial", () => {
    expect(answeredInPart(1, 1)).toBe(", and only in part");
    expect(answeredInPart(2, 2)).toBe(", both only in part");
    expect(answeredInPart(4, 4)).toBe(", all 4 only in part");
  });
});

describe("truncationNote", () => {
  const capped = { limit: 2000, product_count: 3109, bought_once: 2039, min_purchases: 1 };

  it("names where the alphabetical index stops", () => {
    expect(truncationNote(capped, "PEARS")).toContain(
      "alphabetically, so the index stops at “PEARS”",
    );
  });

  it("offers the filter that fits everything bought more than once", () => {
    expect(truncationNote(capped, "PEARS")).toContain(
      "Set “Bought at least” to 2 to see all 1,070 you bought more than once.",
    );
  });

  it("does not offer the filter when it is already set", () => {
    const note = truncationNote({ ...capped, min_purchases: 2 }, "PEARS");
    expect(note).not.toContain("Bought at least");
    expect(note).toContain("The rest are in the response");
  });

  it("does not offer the filter when the repeats alone overflow the page", () => {
    const note = truncationNote({ ...capped, product_count: 5000, bought_once: 2000 }, null);
    expect(note).not.toContain("Bought at least");
    expect(note).not.toContain("stops at");
  });
});
