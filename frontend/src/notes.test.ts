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
  const capped = { limit: 2000, product_count: 3000, left_out: 1000, left_out_max_purchases: 1 };

  it("says the page keeps the most-bought, not a run of the alphabet", () => {
    expect(truncationNote(capped)).toContain(
      "Showing the 2,000 products bought most often, of 3,000.",
    );
  });

  it("says when only things bought once were left out", () => {
    expect(truncationNote(capped)).toContain("The other 1,000 (each bought once) are left out");
  });

  it("says how often the most-bought of the left-out ones was bought", () => {
    expect(truncationNote({ ...capped, left_out_max_purchases: 3 })).toContain(
      "(none bought more than 3 times)",
    );
  });

  it("points at the search, which reaches past the cap", () => {
    expect(truncationNote(capped)).toContain("“Name contains” still finds any of them");
  });
});
