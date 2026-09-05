/**
 * Save the product index as an image, with no dependency and no network.
 *
 * The index is a field of type, so the image is an SVG of text rather than a
 * rasterised screenshot. That choice is the whole design:
 *
 * - **No library.** The obvious route is html2canvas or dom-to-image, and the
 *   reason not to take it is that this build vendors everything and asserts, in
 *   `tests/test_frontend_build.py`, that the page loads nothing from another
 *   origin. A rasteriser is 50KB-plus of code whose entire job is to
 *   re-implement text layout, and it re-implements it slightly wrong. Laying
 *   out our own lines is about a hundred lines and gets the type exactly right,
 *   because the browser measures it.
 * - **Text, not pixels.** The output is selectable, searchable, scales to any
 *   size, and is a few tens of KB rather than several MB. An archive you cannot
 *   search is a photograph of an archive.
 * - **System fonts, named not embedded.** `DESIGN.md` records that this app
 *   deliberately ships no font files and takes serif and sans from the system.
 *   The SVG names the same stacks, so on the machine that produced it — which
 *   is where a local-first tool's output lives — it renders identically. Opened
 *   somewhere without Georgia it substitutes, the same way the app already does
 *   on Linux, which is a documented and accepted variance rather than a new one.
 */

const FONT_SERIF = 'ui-serif, Georgia, "Iowan Old Style", "Palatino Linotype", serif';
const FONT_SANS =
  'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

/** The on-screen ladder at `lg`, which is the size the index is designed at. */
export const TIER_TYPE: Record<number, { size: number; weight: number }> = {
  5: { size: 48, weight: 600 },
  4: { size: 32, weight: 600 },
  3: { size: 20, weight: 500 },
  2: { size: 17, weight: 400 },
  1: { size: 13, weight: 400 },
};

export type ExportEntry = { description: string; tier: number };
export type Placed = ExportEntry & { x: number; size: number; weight: number };
export type Line = { items: Placed[]; height: number; baseline: number };

export type LayoutOptions = {
  width: number;
  padding: number;
  /** Space after each entry, as a fraction of that entry's own size. A fixed
   *  gap next to a 48px name reads as no gap at all — the same bug the on-screen
   *  index fixed with an em-based margin. */
  gapEm: number;
  lineHeight: number;
};

export const DEFAULTS: LayoutOptions = {
  width: 1200,
  padding: 64,
  gapEm: 0.3,
  lineHeight: 1.35,
};

/** How wide a string is. Injected so the layout is pure and can be tested. */
export type Measure = (text: string, size: number, weight: number) => number;

/**
 * Greedy line-breaking, which is what the browser does to the real index.
 *
 * Each line is as tall as its tallest entry, so a line holding a 48px name is
 * not squeezed to the 13px one beside it. An entry wider than the measure gets
 * a line to itself rather than being dropped or clipped: the on-screen index
 * resolves that case as wrap, and losing a product from an index of everything
 * you bought would be a lie of omission.
 */
export function layoutIndex(
  entries: ExportEntry[],
  measure: Measure,
  options: Partial<LayoutOptions> = {},
): { lines: Line[]; width: number; height: number } {
  const opts = { ...DEFAULTS, ...options };
  const inner = opts.width - opts.padding * 2;
  const lines: Line[] = [];
  let items: Placed[] = [];
  let x = 0;

  const flush = () => {
    if (items.length === 0) return;
    const height = Math.max(...items.map((i) => i.size)) * opts.lineHeight;
    lines.push({ items, height, baseline: height * 0.78 });
    items = [];
    x = 0;
  };

  for (const entry of entries) {
    const type = TIER_TYPE[entry.tier] ?? TIER_TYPE[1];
    const width = measure(entry.description, type.size, type.weight);
    if (x > 0 && x + width > inner) flush();
    items.push({ ...entry, x, size: type.size, weight: type.weight });
    x += width + type.size * opts.gapEm;
  }
  flush();

  const height =
    lines.reduce((total, line) => total + line.height, 0) + opts.padding * 2;
  return { lines, width: opts.width, height };
}

/** XML-escape. A product called `M&M'S` or `5" PIE` otherwise breaks the file. */
export function escapeXml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export type IndexMeta = {
  retailer: string;
  productCount: number;
  coverage: string;
};

/** The whole index as one self-contained SVG document. */
export function indexSvg(
  entries: ExportEntry[],
  meta: IndexMeta,
  measure: Measure,
  options: Partial<LayoutOptions> = {},
): string {
  const opts = { ...DEFAULTS, ...options };
  const headHeight = 96;
  const { lines, width, height } = layoutIndex(entries, measure, options);
  const total = height + headHeight;

  let y = opts.padding + headHeight;
  const body = lines
    .map((line) => {
      y += line.height;
      return line.items
        .map(
          (item) =>
            `<text x="${(opts.padding + item.x).toFixed(1)}" y="${(
              y - line.height + line.baseline
            ).toFixed(1)}" font-size="${item.size}" font-weight="${item.weight}">` +
            `${escapeXml(item.description)}</text>`,
        )
        .join("");
    })
    .join("\n  ");

  // No external references of any kind, so the file is one thing you can move.
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${total.toFixed(
    0,
  )}" viewBox="0 0 ${width} ${total.toFixed(0)}" font-family='${FONT_SERIF}'>
  <rect width="100%" height="100%" fill="#FBFAF7"/>
  <text x="${opts.padding}" y="${opts.padding + 20}" font-size="26" font-weight="600" fill="#1E1C19">${escapeXml(
    meta.retailer,
  )}</text>
  <text x="${opts.padding}" y="${opts.padding + 46}" font-size="13" fill="#78716A" font-family='${FONT_SANS}'>${escapeXml(
    `${meta.productCount} products · ${meta.coverage}`,
  )}</text>
  <line x1="${opts.padding}" y1="${opts.padding + 62}" x2="${
    width - opts.padding
  }" y2="${opts.padding + 62}" stroke="#D8D2C7"/>
  <g fill="#1E1C19">
  ${body}
  </g>
</svg>
`;
}

/** Measure with the browser, using the same stack the SVG will name. */
export function canvasMeasure(): Measure {
  const context = document.createElement("canvas").getContext("2d");
  if (!context) return (text, size) => text.length * size * 0.5;
  return (text, size, weight) => {
    context.font = `${weight} ${size}px ${FONT_SERIF}`;
    return context.measureText(text).width;
  };
}

/** Hand the file to the reader. Nothing leaves the machine. */
export function downloadSvg(svg: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  // Revoked on the next tick: revoking synchronously races the download in
  // Firefox, which reads the blob after the click handler returns.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
