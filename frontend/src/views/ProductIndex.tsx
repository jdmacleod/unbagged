import { useState } from "react";
import { api } from "../api";
import { useAsync } from "../components/useAsync";
import { Aside, Empty, ErrorBox, Spine, Spinner } from "../components/ui";
import { number } from "../format";
import type { IndexEntry, IndexTier, ProductIndex as Index } from "../types";

/**
 * Every product you bought, by name, sized by how often. See DESIGN.md.
 *
 * Not a chart and not a ranking. Prices already sorts by purchases and answers
 * "what do I buy most" precisely; a cloud sized by the same number is strictly
 * worse at that job. What this screen shows is the *names* — the retailer's own
 * truncated all-caps abbreviations for everyday groceries — several hundred at
 * once. That is not a frequency chart, it is a portrait of the vocabulary a
 * company uses to describe your life, and nothing else in the app does it.
 *
 * The order is alphabetical, which is the one order that is not a ranking. That
 * only pays out if the reader can *use* the alphabet, which is what the thumb
 * rail and the drop capitals are for: a printed index is scannable because it
 * has a constant edge and a thumb tab, not merely because it is sorted.
 *
 * Set ragged right. Justifying a line that holds both a 13px and a 48px word
 * opens rivers wide enough to read as a rendering accident, and the tail is two
 * thirds of the field, so the failure would be at maximum area.
 */
export function ProductIndex({
  requestId,
  onOpenProduct,
}: {
  requestId: number;
  /** Builds the href for a product's filtered timeline. Takes the whole entry,
   *  not just the name: the timeline's search is a substring match, so linking
   *  by name pulls in every product whose name *contains* this one. See the
   *  note on Block. Kept out of this view so the URL schema lives in one place. */
  onOpenProduct: (entry: IndexEntry) => string;
}) {
  const [q, setQ] = useState("");
  const [minPurchases, setMinPurchases] = useState(1);

  const index = useAsync(
    () => api.productIndex(requestId, { q, min_purchases: minPurchases }),
    [requestId, q, minPurchases],
  );

  if (index.error) return <ErrorBox error={index.error} />;
  if (!index.data) return <Spinner label="Reading the index" />;

  return (
    <IndexBody
      data={index.data}
      q={q}
      setQ={setQ}
      minPurchases={minPurchases}
      setMinPurchases={setMinPurchases}
      onOpenProduct={onOpenProduct}
    />
  );
}

/**
 * Tier to type size. Five absolute steps off the ladder in DESIGN.md, never a
 * continuous ramp: on a continuous scale 28.3% of comparable pairs painted more
 * ink for the smaller number, because a long name set small out-inks a short
 * name set large. Quantised, that is 0%.
 *
 * Written out rather than interpolated — Tailwind scans source for whole class
 * names and a template literal produces nothing at build time.
 *
 * Below `lg` the top tier caps at 28px. At 375px the measure is about 327px and
 * a 21-character name at 48px needs roughly 500, so uncapped it would break
 * mid-name. Colour is held constant across every tier and only size and weight
 * vary: the tail must not be both the smallest and the faintest thing on screen
 * or it drops under the contrast floor.
 */
const TIER_CLASS: Record<number, string> = {
  5: "text-[28px] font-semibold lg:text-[48px]",
  4: "text-[24px] font-semibold lg:text-[32px]",
  3: "text-[19px] font-medium lg:text-[20px]",
  2: "text-[16px] lg:text-[17px]",
  1: "text-[13px]",
};

/** The same ladder at legend scale, so the sample reads as the same steps. */
const LEGEND_CLASS: Record<number, string> = {
  5: "text-[19px] font-semibold",
  4: "text-[16px] font-semibold",
  3: "text-[14px] font-medium",
  2: "text-[12px]",
  1: "text-[11px]",
};

const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");

/** The bucket a product files under. Anything not A-Z files under "#". */
function initial(entry: IndexEntry): string {
  const first = entry.description.trim().charAt(0).toUpperCase();
  return ALPHABET.includes(first) ? first : "#";
}

/** "12+", "7–11", "2–3", "once". Derived from the tier floors the API sends,
 *  so the legend cannot drift from the encoding it describes. */
function tierRange(tiers: IndexTier[], tier: number): string {
  const floor = tiers.find((t) => t.tier === tier)?.min_purchases ?? 1;
  const above = tiers.find((t) => t.tier === tier + 1)?.min_purchases;
  if (floor === 1) return "once";
  if (above === undefined) return `${floor}+`;
  return above - 1 === floor ? `${floor}` : `${floor}–${above - 1}`;
}

function IndexBody({
  data,
  q,
  setQ,
  minPurchases,
  setMinPurchases,
  onOpenProduct,
}: {
  data: Index;
  q: string;
  setQ: (v: string) => void;
  minPurchases: number;
  setMinPurchases: (n: number) => void;
  onOpenProduct: (entry: IndexEntry) => string;
}) {
  // The filters live outside every empty-state branch on purpose. Returning a
  // bare empty state unmounts them, which leaves the reader looking at "widen
  // the filter" with no filter on screen and no escape but a reload. That exact
  // bug shipped once already on Prices.
  const filters = (
    <Filters
      q={q}
      setQ={setQ}
      minPurchases={minPurchases}
      setMinPurchases={setMinPurchases}
    />
  );

  // A retailer that answered with a letter disclosed no purchases at all.
  // Rendering that as an empty index states "you bought nothing", which is a
  // claim about you rather than the silence it actually was.
  if (!data.disclosed) {
    return (
      <Spine margin={<Aside>no purchase data</Aside>}>
        <h2 className="font-serif text-[17px] font-semibold">Nothing to show here</h2>
        <p className="mt-2 max-w-[62ch] text-muted">
          This response contained no purchase data, so there are no products to
          list. That is not the same as having bought nothing: the retailer did not
          disclose the specific pieces of personal information it holds.
        </p>
        <p className="mt-2 max-w-[62ch] text-muted">
          The absence is recorded as a finding in the <strong>Compliance</strong>{" "}
          view, which is where a response like this is worth reading.
        </p>
      </Spine>
    );
  }

  const letters = new Set(data.products.map(initial));
  const stopped = data.products.filter((p) => p.stopped);

  return (
    <div className="space-y-8">
      <Spine margin={<Aside>{number(data.total_products)} products</Aside>}>
        <Headline data={data} />
        <div className="mt-4">{filters}</div>
      </Spine>

      {data.product_count === 0 ? (
        <Spine>
          <Empty>
            {/* The fourth case is not "no match", it is "nothing to match
                against": no filter is set and the response still disclosed no
                products. Falling through to the q branch printed
                `No product name contains ""`. */}
            {q && minPurchases > 1
              ? `No product name contains “${q}” and was bought at least ${number(minPurchases)} times.`
              : q
                ? `No product name contains “${q}”.`
                : minPurchases > 1
                  ? `No product was bought at least ${number(minPurchases)} times.`
                  : "This response disclosed no products."}
          </Empty>
        </Spine>
      ) : (
        <Spine marginFirst margin={<Rail letters={letters} tiers={data.tiers} />}>
          {/* Below lg the margin does not exist, so the rail comes inline as a
              strip. Without this the jump control and the legend simply vanish
              on a phone, which is how a fix has been silently lost here before. */}
          <StripRail letters={letters} />

          <a
            href="#after-the-index"
            className="sr-only focus:not-sr-only focus:mb-3 focus:inline-block focus:rounded-[2px] focus:border focus:border-line focus:px-3 focus:py-1.5"
          >
            Skip the index ({number(data.product_count)} products)
          </a>

          <Block products={data.products} onOpenProduct={onOpenProduct} />

          <InlineLegend tiers={data.tiers} />

          {data.truncated && (
            <p className="num mt-4 max-w-[62ch] text-[11.5px] text-faint">
              Showing {number(data.limit)} of {number(data.product_count)} products.
              The rest are in the response; this page stops here so it stays
              readable.
            </p>
          )}

          {/* `tabIndex={-1}` is what makes the skip link a skip link. Without
              it the target is not focusable, so the browser scrolls the page
              and leaves the keyboard exactly where it was: the next Tab went
              into entry 1 of 399 and the control was decoration. Measured, the
              jump rail sits 400 tab stops past this point, so this is the only
              way to reach it without walking the whole index. */}
          <div id="after-the-index" tabIndex={-1} className="outline-none" />
        </Spine>
      )}

      {stopped.length > 0 && <Stopped entries={stopped} staleBefore={data.stale_before} />}
    </div>
  );
}

/**
 * The hanging figure, as every other view opens.
 *
 * There is a real tension here with DESIGN.md's stated first three seconds —
 * recognition first, the unease later — because a statistic above the portrait
 * spends the recognition beat on analysis. It stays: 68% is the most striking
 * fact the response contains and it is otherwise invisible, and the same mark
 * opens Profile, Compliance, Compare and Prices. Recorded rather than lost.
 */
function Headline({ data }: { data: Index }) {
  return (
    <div className="flex items-baseline gap-5">
      <span className="font-serif text-[42px] leading-none font-semibold text-faint tabular-nums">
        {number(data.bought_once_total)}
      </span>
      <div className="min-w-0 flex-1">
        <h2 className="font-serif text-[17px] font-semibold">
          of {number(data.total_products)} products you bought exactly once
        </h2>
        <p className="mt-0.5 max-w-[62ch] text-muted">
          Two years of shopping, in the words the retailer files it under. Size is
          how often you bought it, in five steps. Most of this page is a single
          trip.
        </p>
      </div>
    </div>
  );
}

function Filters({
  q,
  setQ,
  minPurchases,
  setMinPurchases,
}: {
  q: string;
  setQ: (v: string) => void;
  minPurchases: number;
  setMinPurchases: (n: number) => void;
}) {
  // Visible labels on both. A placeholder is not a label; it disappears the
  // moment you type.
  const field =
    "rounded-[2px] border border-line bg-transparent px-2 py-1.5 text-ink " +
    "focus:border-accent focus:outline-2 focus:outline-offset-1 focus:outline-accent";
  const legend =
    "flex flex-col gap-1 text-[11.5px] tracking-[0.05em] text-muted uppercase";
  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className={legend}>
        Name contains
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="product or UPC"
          className={`${field} w-44 normal-case`}
        />
      </label>
      <label className={legend}>
        Bought at least
        <input
          type="number"
          min={1}
          max={40}
          value={minPurchases}
          onChange={(e) => setMinPurchases(Number(e.target.value) || 1)}
          className={`${field} num w-16`}
        />
      </label>
    </div>
  );
}

/**
 * The index itself: one continuous ruled field of names.
 *
 * Entries are anchors inside list items, never buttons. Two reasons, and the
 * first is measurable: the coarse-pointer rule in index.css sets a 44px minimum
 * height on every `button`, and exempts an `a` sitting inside `p`, `li` or `td`.
 * At 353 entries that is the difference between a ~4,300px page and a ~16,700px
 * one on a phone. The second is that these navigate, so they want a real href —
 * middle-click, open-in-new-tab and the browser's own history all come free, and
 * the URL schema is exercised by the link rather than by a click handler.
 *
 * The drop capital is `aria-hidden`: the entries are already in alphabetical
 * order and a screen reader announces them in it, so the letter is a visual
 * affordance rather than content. It carries the jump target.
 *
 * The link filters the timeline by UPC, never by name. Timeline's search is a
 * substring match, and this catalogue is full of names that contain each other:
 * clicking BANANAS EA matched ORGANIC BANANAS EA and SIMPLE TRUTH ORG BANANAS
 * EA as well, so a product bought 20 times opened a timeline claiming 26 visits
 * "that included" it. A UPC is exact and is what a click on one entry means.
 */
function Block({
  products,
  onOpenProduct,
}: {
  products: IndexEntry[];
  onOpenProduct: (entry: IndexEntry) => string;
}) {
  let lastLetter = "";
  return (
    // `overflow-wrap: anywhere` is the safety net, not the normal path. Each
    // name is joined with non-breaking spaces so it never splits across lines —
    // a product name broken in half is unreadable in a field of several hundred
    // — and `anywhere` only engages if a single name is wider than the whole
    // measure, at which point wrapping beats scrolling the page sideways. That
    // is the "wrap or cap" the edge-case table asked for, resolved as wrap.
    <ul className="leading-[1.35] [overflow-wrap:anywhere]">
      {products.map((entry) => {
        const letter = initial(entry);
        const opensLetter = letter !== lastLetter;
        lastLetter = letter;
        return (
          <li
            key={entry.upc}
            // Inline, so the whole thing reads as one field of type rather than
            // as a stack of rows. `scroll-mt` clears the sticky strip that the
            // jump rail becomes below lg.
            className="inline scroll-mt-16 lg:scroll-mt-4"
            id={opensLetter ? `letter-${letter}` : undefined}
          >
            {opensLetter && (
              <span
                aria-hidden
                className="mr-1.5 font-serif text-[22px] font-semibold text-faint"
              >
                {letter}
              </span>
            )}
            <a
              href={onOpenProduct(entry)}
              title={`${entry.description} · bought ${entry.purchases} ${
                entry.purchases === 1 ? "time" : "times"
              }`}
              // `mr` in em, so the gap grows with the entry. The break between
              // items is a literal space, and a space is set at the *parent's*
              // 13px — next to a 48px name that reads as no gap at all, and two
              // large neighbours ran together into "BANANAS 3LBBANANAS 5LB".
              className={`mr-[0.3em] hover:text-accent hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                TIER_CLASS[entry.tier] ?? TIER_CLASS[1]
              }`}
            >
              {entry.description.replace(/ /g, "\u00a0")}
              {/* Size conveys nothing to a screen reader, so the count travels
                  in the accessible name instead. */}
              <span className="sr-only">
                , bought {entry.purchases}{" "}
                {entry.purchases === 1 ? "time" : "times"}
              </span>
            </a>
            {/* A real space between entries. Without it the browser has no
                break opportunity anywhere in the list — adjacent anchors with a
                margin between them are still one unbroken run — and the whole
                index lays out on a single 82,000px line. */}{" "}
          </li>
        );
      })}
    </ul>
  );
}

/** The thumb index and the size legend, in the margin, at lg and up. */
function Rail({ letters, tiers }: { letters: Set<string>; tiers: IndexTier[] }) {
  return (
    <div className="sticky top-4 pt-2">
      <div className="num border-b border-rule pb-1 text-[11.5px] text-faint">jump</div>
      <div className="num mt-2 flex flex-wrap gap-x-2 gap-y-0.5 text-[12px]">
        {["#", ...ALPHABET].map((letter) =>
          letters.has(letter) ? (
            <a
              key={letter}
              href={`#letter-${letter}`}
              className="text-accent hover:underline"
            >
              {letter}
            </a>
          ) : (
            // Present but unavailable, the way a thumb index is cut into every
            // letter whether or not the volume has entries under it.
            <span key={letter} aria-hidden className="text-rule">
              {letter}
            </span>
          ),
        )}
      </div>
      <Legend tiers={tiers} className="mt-4 border-t border-rule pt-2" />
    </div>
  );
}

/** Below lg the margin is gone, so the jump rail comes inline as a strip. */
function StripRail({ letters }: { letters: Set<string> }) {
  return (
    <div className="num sticky top-0 z-10 -mx-1 mb-3 flex flex-wrap gap-x-2 gap-y-0.5 border-b border-rule bg-page px-1 py-2 text-[12px] lg:hidden">
      {["#", ...ALPHABET].map((letter) =>
        letters.has(letter) ? (
          <a key={letter} href={`#letter-${letter}`} className="text-accent">
            {letter}
          </a>
        ) : (
          <span key={letter} aria-hidden className="text-rule">
            {letter}
          </span>
        ),
      )}
    </div>
  );
}

/**
 * What a size means.
 *
 * The only thing that decodes the encoding for a sighted reader. Without it,
 * size is a mark nobody can read: the screen-reader path carries the count in
 * the accessible name, and everyone else was left guessing.
 */
function Legend({ tiers, className = "" }: { tiers: IndexTier[]; className?: string }) {
  return (
    <div className={className}>
      <div className="num text-[11.5px] text-faint">size = times bought</div>
      {[5, 4, 3, 2, 1].map((tier) => (
        <div key={tier} className="mt-1 flex items-baseline gap-2">
          <span aria-hidden className={`${LEGEND_CLASS[tier]} w-7 shrink-0`}>
            Aa
          </span>
          <span className="num text-[11px] text-faint">{tierRange(tiers, tier)}</span>
        </div>
      ))}
    </div>
  );
}

/** The same legend, one line, for the viewports with no margin to put it in. */
function InlineLegend({ tiers }: { tiers: IndexTier[] }) {
  return (
    <p className="mt-4 flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-rule pt-2 text-[11.5px] text-faint lg:hidden">
      <span className="num">size = times bought</span>
      {[5, 4, 3, 2, 1].map((tier) => (
        <span key={tier} className="flex items-baseline gap-1">
          <span aria-hidden className={LEGEND_CLASS[tier]}>
            Aa
          </span>
          <span className="num">{tierRange(tiers, tier)}</span>
        </span>
      ))}
    </p>
  );
}

/**
 * What you used to buy and have not bought since.
 *
 * A separate list rather than a mark inside the field above, because the field
 * has no channel left: size is spent on frequency, weight on tail legibility,
 * and DESIGN.md forbids colour for a meaning like this one. Adding a fourth
 * encoding to a screen already carrying three would be the point at which the
 * portrait becomes a chart.
 *
 * Worded as an observation about dates and never as a claim about behaviour. A
 * seasonal product, a brand switch and an abandoned habit are indistinguishable
 * in this data, and the response does not say which.
 */
function Stopped({
  entries,
  staleBefore,
}: {
  entries: IndexEntry[];
  staleBefore: string | null;
}) {
  return (
    <Spine margin={<Aside>{number(entries.length)} of them</Aside>}>
      <h3 className="font-serif text-[17px] font-semibold">
        Bought more than once, and not since
      </h3>
      <p className="mt-0.5 mb-4 max-w-[62ch] text-muted">
        These were part of the shopping and then stopped appearing, with nothing
        after <span className="num">{staleBefore}</span> and the coverage window
        still running. The response records the dates, not the reason: a season
        ending, a brand switched and a habit dropped look identical here.
      </p>
      <ul className="border-t border-rule">
        {entries.map((entry) => (
          <li
            key={entry.upc}
            className="flex items-baseline gap-4 border-b border-rule py-2"
          >
            <span className="min-w-0 flex-1 truncate" title={entry.description}>
              {entry.description}
            </span>
            <span className="num shrink-0 text-[12.5px] text-muted">
              {entry.purchases}×
            </span>
            <span className="num w-24 shrink-0 text-right text-[12.5px] text-faint">
              {entry.last_seen}
            </span>
          </li>
        ))}
      </ul>
    </Spine>
  );
}
