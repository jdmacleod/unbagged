const currency = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

export const money = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : currency.format(value);

export const number = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : value.toLocaleString();

/** The report gives a store-local wall clock with no timezone, so it is rendered
 *  exactly as given. Parsing it as UTC would move an evening shop to the next
 *  day; see the Kroger adapter's NOTES.md. */
export const day = (iso: string | null | undefined) => iso?.slice(0, 10) ?? "—";
export const clock = (iso: string | null | undefined) => iso?.slice(11, 16) ?? "";
export const dayAndTime = (iso: string | null | undefined) =>
  iso ? `${day(iso)} ${clock(iso)}`.trim() : "—";

export const percent = (value: number | null | undefined) =>
  value === null || value === undefined
    ? "—"
    : `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;

/** "householdComposition" -> "Household composition", "ordinal_1_7" -> "Ordinal 1-7" */
export const humanise = (label: string) => {
  const spaced = label
    // A digit-underscore-digit run is a range, not a word boundary.
    .replace(/(\d)_(\d)/g, "$1\u2013$2")
    .replace(/_/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
};

/** Stable categorical colour for a key.
 *
 *  Identity, not severity: which store, which series, which product group. The
 *  same key always gets the same hue across views and across reloads, because
 *  recognition is the whole point — you should be able to learn that your
 *  Tuesday store is the green one. See DESIGN.md on what colour may mean.
 *
 *  Hashed rather than index-assigned so a store keeps its colour when the list
 *  it sits in is filtered or reordered.
 */
export const CATEGORY_COUNT = 6;

export function categoryIndex(key: string): number {
  let hash = 0;
  for (let i = 0; i < key.length; i++) {
    hash = (hash * 31 + key.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % CATEGORY_COUNT;
}

/** Tailwind text colour class for a key's category hue. */
export function categoryText(key: string): string {
  // Written out rather than interpolated: Tailwind scans source for whole class
  // names, and a template literal produces nothing at build time.
  return [
    "text-cat-1", "text-cat-2", "text-cat-3",
    "text-cat-4", "text-cat-5", "text-cat-6",
  ][categoryIndex(key)];
}

/** The raw CSS variable, for SVG stroke and fill where a class will not do. */
export function categoryVar(key: string): string {
  return `var(--cat-${categoryIndex(key) + 1})`;
}
