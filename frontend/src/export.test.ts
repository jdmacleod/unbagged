import { describe, expect, it } from "vitest";
import {
  DEFAULTS,
  escapeXml,
  indexSvg,
  layoutIndex,
  type ExportEntry,
} from "./export";

/** A predictable stand-in for the browser: every glyph is half the font size. */
const measure = (text: string, size: number) => text.length * size * 0.5;

const entry = (description: string, tier = 1): ExportEntry => ({ description, tier });

describe("layoutIndex", () => {
  it("packs entries onto a line until the measure runs out", () => {
    // 1200 wide, 64 padding each side -> 1072 inner. At tier 1 (13px) each
    // 10-character name is 65px plus a 3.9px gap.
    const entries = Array.from({ length: 40 }, () => entry("ABCDEFGHIJ"));
    const { lines } = layoutIndex(entries, measure);
    expect(lines.length).toBeGreaterThan(1);
    expect(lines[0].items.length).toBeGreaterThan(10);
  });

  it("never drops an entry, whatever the width", () => {
    // An index of everything you bought that quietly omits something is a lie
    // of omission, so the count out must equal the count in.
    const entries = Array.from({ length: 137 }, (_, i) => entry(`ITEM ${i}`, (i % 5) + 1));
    const { lines } = layoutIndex(entries, measure);
    expect(lines.flatMap((l) => l.items)).toHaveLength(137);
  });

  it("gives an entry wider than the measure a line of its own", () => {
    const { lines } = layoutIndex(
      [entry("SHORT"), entry("X".repeat(400), 5), entry("ALSO SHORT")],
      measure,
    );
    const alone = lines.find((l) => l.items.some((i) => i.description.length === 400));
    expect(alone!.items).toHaveLength(1);
  });

  it("makes a line as tall as its tallest entry", () => {
    // A line holding a 48px name must not be squeezed to the 13px one beside it.
    const { lines } = layoutIndex([entry("BIG", 5), entry("small", 1)], measure);
    expect(lines[0].height).toBeCloseTo(48 * DEFAULTS.lineHeight);
  });

  it("scales the gap with the entry, not with the page", () => {
    // A fixed gap next to a 48px name reads as no gap: the same bug the
    // on-screen index fixed with an em-based margin.
    const { lines } = layoutIndex([entry("A", 5), entry("B", 5)], measure);
    const [first, second] = lines[0].items;
    expect(second.x - (first.x + measure("A", 48))).toBeCloseTo(48 * DEFAULTS.gapEm);
  });

  it("handles an empty index without inventing a line", () => {
    const { lines, height } = layoutIndex([], measure);
    expect(lines).toEqual([]);
    expect(height).toBe(DEFAULTS.padding * 2);
  });
});

describe("escapeXml", () => {
  it("escapes what would otherwise break the document", () => {
    // Real product names carry all of these.
    expect(escapeXml("M&M'S")).toBe("M&amp;M'S");
    expect(escapeXml('5" PIE')).toBe("5&quot; PIE");
    expect(escapeXml("A<B>C")).toBe("A&lt;B&gt;C");
  });

  it("escapes the ampersand first, so escapes are not double-escaped", () => {
    expect(escapeXml("&lt;")).toBe("&amp;lt;");
  });
});

describe("indexSvg", () => {
  const meta = { retailer: "Kroger", productCount: 3, coverage: "2024-02 → 2026-01" };

  // No DOMParser here on purpose. Adding jsdom to check the output of a feature
  // whose whole justification is "no dependency" would be a poor trade, and the
  // stronger check exists anyway: tests/container/test_layout.py loads the real
  // exported file into Chromium and asserts it decodes as an image. These cover
  // the string-building; that covers whether it is a document.
  const textNodes = (svg: string) => svg.match(/<text\b/g) ?? [];

  it("survives a product name full of markup characters", () => {
    const svg = indexSvg([entry('M&M\'S 5" <BIG>')], meta, measure);
    expect(svg).toContain("M&amp;M'S 5&quot; &lt;BIG&gt;");
    // The raw forms must not survive, or the document is malformed.
    expect(svg).not.toContain("<BIG>");
    expect(svg.match(/M&M/)).toBe(null);
  });

  it("references nothing it would have to fetch", () => {
    // The build asserts the page loads nothing from another origin; a saved file
    // that phones home would walk that back at one remove. The SVG namespace is
    // an identifier, not a fetch, so it is excluded rather than counted.
    const svg = indexSvg([entry("BANANAS")], meta, measure);
    const withoutNamespace = svg.replace(/xmlns="[^"]*"/g, "");
    expect(withoutNamespace).not.toMatch(/https?:\/\//);
    expect(svg).not.toMatch(/<image|xlink:href|@import|url\(/);
  });

  it("carries the heading a printed index needs to be worth keeping", () => {
    const svg = indexSvg([entry("BANANAS")], meta, measure);
    expect(svg).toContain("Kroger");
    expect(svg).toContain("3 products");
    expect(svg).toContain("2024-02 → 2026-01");
  });

  it("writes every entry into the document", () => {
    const entries = Array.from({ length: 60 }, (_, i) => entry(`ITEM${i}`, (i % 5) + 1));
    const svg = indexSvg(entries, meta, measure);
    // Two of the text nodes are the heading and its subtitle.
    expect(textNodes(svg)).toHaveLength(62);
    expect(svg).toContain(">ITEM59<");
  });

  it("grows the canvas to fit rather than clipping", () => {
    const few = indexSvg([entry("A")], meta, measure);
    const many = indexSvg(
      Array.from({ length: 300 }, () => entry("SOMETHING LONGER", 4)),
      meta,
      measure,
    );
    const height = (svg: string) => Number(svg.match(/height="(\d+)"/)![1]);
    expect(height(many)).toBeGreaterThan(height(few));
  });
});
