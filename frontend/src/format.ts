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
export const clock = (iso: string | null | undefined) =>
  iso?.slice(11, 16) ?? "";
export const dayAndTime = (iso: string | null | undefined) =>
  iso ? `${day(iso)} ${clock(iso)}`.trim() : "—";

/** A loyalty saving, in the column where savings live.
 *
 *  Three outcomes, not two, and telling the middle one apart is the whole point.
 *  The em dash means "the retailer did not say" and nothing else — see the
 *  em-dash row in DESIGN.md's decisions log. A saving of exactly zero is not
 *  silence: it is a disclosed fact about a line that was sold at full price, and
 *  most lines are. So it renders blank rather than borrowing the mark for
 *  absence. `$0.00` down a whole column reads as broken, which is why the dash
 *  was reached for in the first place; blank says the same thing without
 *  spending a mark that now means something else.
 */
export const saving = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : value ? `−${money(value)}` : "";

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

/** What to call the money, given whether line items were disclosed.
 *
 *  "Paid" means a summed line amount everywhere in this app. A response that
 *  disclosed basket totals and no lines carries the retailer's own stated
 *  totals instead, which is a different quantity — so the word changes with the
 *  figure rather than one word covering two things.
 *
 *  One definition because three surfaces need the same answer and had been
 *  making it separately: the Timeline header, the month bar axis, and the
 *  Compare row. Two of them agreed and the third did not, which is what issue
 *  #57 was: one figure under two names depending on which tab you were on.
 *
 *  The SCOPE of the question stays with the caller and is deliberately not
 *  shared. The Timeline header asks about the whole response; the bar axis asks
 *  about the months actually drawn, because the bars really are drawn from what
 *  is in view; Compare asks about the columns present. Those are three
 *  different predicates over the same vocabulary, and collapsing them would
 *  make the axis lie about what it is measuring.
 */
export const paidWord = (linesDisclosed: boolean) =>
  linesDisclosed ? "paid" : "spent";

/** The headline figure and the Compare row: "Total paid" / "Total spent". */
export const paidLabel = (linesDisclosed: boolean) =>
  `Total ${paidWord(linesDisclosed)}`;

/** The month bar axis, which is lower case and says what it is per: "spent, by month". */
export const barAxisLabel = (anyItemised: boolean) =>
  `${paidWord(anyItemised)}, by month`;

/** Stable categorical colour for a key.
 *
 *  Identity, not severity: which store, which series, which product group. The
 *  same key gets the same hue across views and across reloads, because
 *  recognition is the whole point — you should be able to learn that your
 *  Tuesday store is the green one. See DESIGN.md on what colour may mean.
 *
 *  Hashed rather than index-assigned so a store keeps its colour when the list
 *  it sits in is filtered or reordered.
 *
 *  **One exception, and only one.** `assignStoreHues` below breaks a tie when
 *  two keys in the same response land on the same hue. Everything that does not
 *  collide still comes from this hash, which is why the guarantee above is
 *  written as "the same key gets the same hue" rather than "always". A colliding
 *  store can change colour when a different response is loaded.
 */
export const CATEGORY_COUNT = 6;

export function categoryIndex(key: string): number {
  let hash = 0;
  for (let i = 0; i < key.length; i++) {
    hash = (hash * 31 + key.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % CATEGORY_COUNT;
}

/** The raw CSS variable, for SVG stroke and fill where a class will not do. */
export function categoryVar(key: string): string {
  return `var(--cat-${categoryIndex(key) + 1})`;
}

/** Hue indices ordered by how far apart they stay, worst pair first.
 *
 *  Measured on the palette in `index.css` as CIEDE2000 distance, taking the
 *  worst of normal, deuteranopic and protanopic vision (Viénot 1999). The order
 *  comes out identical in light and dark.
 *
 *  It matters because "pick any free index" is not the same as "pick a
 *  different colour". `--cat-2` and `--cat-4` are ΔE 1.1 apart under
 *  deuteranopia — indistinguishable at the 8px dot the timeline draws — so a
 *  de-collision that only avoids reusing an index can change nothing at all for
 *  roughly 1 in 12 men, while its test passes because the numbers differ.
 *
 *  What the order buys, worst pair across the three vision models:
 *  2 keys ΔE 43.7 · 3 keys 14.4 · 4 keys 6.0 · 5 keys 4.8 · 6 keys 1.1.
 *  So colour separates confidently at two and three, is normal-vision-only from
 *  four to six, and past six there is nothing left to give. The label carries
 *  the meaning at every count — see DESIGN.md — which is why this degrades
 *  rather than breaks.
 */
const SPREAD_ORDER = [2, 3, 0, 5, 4, 1];

/** Hue index per key, hash-assigned, with collisions broken by separation.
 *
 *  The hash still assigns. Only a key that lands on a hue already taken moves,
 *  and it moves to the first free index in `SPREAD_ORDER` rather than to
 *  whatever happened to be next. A set with no collisions returns exactly what
 *  `categoryIndex` alone would have returned.
 *
 *  Keys are processed in sorted order so the outcome does not depend on the
 *  order they arrive in — the caller passes `stats.stores`, which is sorted by
 *  visit count, and a store that gains a visit must not repaint the legend.
 *
 *  Past `CATEGORY_COUNT` keys there is no free index to move to and collisions
 *  are unavoidable; the hash's answer stands, which is the same behaviour this
 *  had before.
 */
export function assignStoreHues(keys: string[]): Map<string, number> {
  const assigned = new Map<string, number>();
  const taken = new Set<number>();
  const collided: string[] = [];

  for (const key of [...new Set(keys)].sort()) {
    const wanted = categoryIndex(key);
    if (taken.has(wanted)) {
      collided.push(key);
    } else {
      taken.add(wanted);
      assigned.set(key, wanted);
    }
  }

  for (const key of collided) {
    const free = SPREAD_ORDER.find((i) => !taken.has(i));
    // Undefined once every hue is spoken for. Keeping the hash's answer means
    // the collision is visible rather than the store being unpainted.
    const index = free ?? categoryIndex(key);
    taken.add(index);
    assigned.set(key, index);
  }

  return assigned;
}

/** The CSS variable for an assigned index, or for a key with no assignment.
 *
 *  A basket can carry a store code that is not in `stats.stores` — that query
 *  excludes nulls — so this falls back to the plain hash rather than returning
 *  undefined and painting a transparent dot.
 */
export function storeVar(hues: Map<string, number>, key: string): string {
  return `var(--cat-${(hues.get(key) ?? categoryIndex(key)) + 1})`;
}
