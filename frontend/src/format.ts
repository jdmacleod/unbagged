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
 *  two keys in the same response want hues a reader cannot tell apart — whether
 *  that is the same index or two that merely look identical. Everything that
 *  does not collide still comes from this hash, which is why the guarantee above
 *  is written as "the same key gets the same hue" rather than "always". A
 *  colliding store can change colour when a different response is loaded.
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

/** Worst-case perceived distance between two hues, as CIEDE2000.
 *
 *  Measured on the palette in `index.css`, taking the minimum across both
 *  themes and normal, deuteranopic and protanopic vision (Viénot 1999). Below
 *  about 10 two hues read as the same colour at the 8px dot the timeline draws.
 *
 *  The table is the whole reason this file does anything cleverer than "pick a
 *  free index". Six distinct indices are not six distinct colours: `--cat-2`
 *  and `--cat-4` are 1.1 apart under deuteranopia, and `--cat-2`/`--cat-6` are
 *  2.8 apart under protanopia. A reader with red-green colour blindness — about
 *  1 in 12 men — sees this palette as roughly three hues, not six.
 *
 *  Regenerate rather than hand-edit if the palette ever changes; the numbers
 *  are meaningless against different hex values.
 */
const HUE_DISTANCE: readonly (readonly number[])[] = [
  [0.0, 20.6, 13.6, 23.8, 4.8, 15.6],
  [20.6, 0.0, 36.1, 1.1, 24.4, 2.8],
  [13.6, 36.1, 0.0, 43.7, 11.9, 38.5],
  [23.8, 1.1, 43.7, 0.0, 25.2, 6.0],
  [4.8, 24.4, 11.9, 25.2, 0.0, 17.1],
  [15.6, 2.8, 38.5, 6.0, 17.1, 0.0],
];

/** Below this, two hues are the same colour as far as a reader is concerned. */
const INDISTINGUISHABLE = 10;

/** Tie-break order when two free hues are equally far from what is on screen.
 *
 *  The measured maximum-separation order, so a tie resolves toward the hues
 *  that leave the most room for whatever is assigned next.
 */
const SPREAD_ORDER = [2, 3, 0, 5, 4, 1];

/** Hue index per key: hash-assigned, with indistinguishable hues pushed apart.
 *
 *  The hash still assigns. A key only moves when the hue it wants cannot be
 *  told apart from one already on screen — which is a wider test than "that
 *  index is taken", and deliberately so. Two stores rendered `--cat-2` and
 *  `--cat-4` have different indices and the same colour, and the reader this
 *  exists for cannot use the difference. A set with nothing too close together
 *  returns exactly what `categoryIndex` alone would have returned.
 *
 *  A key that has to move takes the free hue that stays furthest from every
 *  hue already assigned, rather than the next one in some fixed order: what
 *  counts is the distance to what is actually on screen.
 *
 *  Keys are processed in sorted order so the result does not depend on the
 *  order they arrive in — the caller passes `stats.stores`, sorted by visit
 *  count, and a store gaining a visit must not repaint the legend.
 *
 *  **What this is worth, measured over random store sets.** At two and three
 *  stores every pair clears the threshold, every time. At four and above it
 *  converges on the palette's own ceiling — the best any assignment can do with
 *  four of these six hues is 6.0, and five or six is worse — so colour stops
 *  being a reliable channel there and the label beside it carries the meaning.
 *  Past six there is no free hue at all and the hash's answer stands. See
 *  DESIGN.md; the ladder is written down rather than the palette being claimed
 *  to scale.
 */
export function assignStoreHues(keys: string[]): Map<string, number> {
  const assigned = new Map<string, number>();
  const taken: number[] = [];
  const moved: string[] = [];

  const clears = (hue: number) =>
    taken.every(
      (t) => t !== hue && HUE_DISTANCE[hue][t] >= INDISTINGUISHABLE,
    );

  for (const key of [...new Set(keys)].sort()) {
    const wanted = categoryIndex(key);
    if (clears(wanted)) {
      taken.push(wanted);
      assigned.set(key, wanted);
    } else {
      moved.push(key);
    }
  }

  for (const key of moved) {
    const free = SPREAD_ORDER.filter((i) => !taken.includes(i));
    if (free.length === 0) {
      // Every hue is spoken for. Keeping the hash's answer makes the collision
      // visible rather than leaving the store unpainted.
      assigned.set(key, categoryIndex(key));
      continue;
    }
    // Furthest from everything already on screen. SPREAD_ORDER is the input
    // order, so an exact tie resolves the same way every time.
    const best = free.reduce((a, b) =>
      nearest(taken, b) > nearest(taken, a) ? b : a,
    );
    taken.push(best);
    assigned.set(key, best);
  }

  return assigned;
}

/** Distance from `hue` to the closest hue already assigned. */
function nearest(taken: number[], hue: number): number {
  return taken.reduce(
    (min, t) => Math.min(min, HUE_DISTANCE[hue][t]),
    Number.POSITIVE_INFINITY,
  );
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
