import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useAsync } from "../components/useAsync";
import { Aside, Cite, Empty, ErrorBox, Spine, Spinner } from "../components/ui";
import { useShowMore } from "../components/ShowMore";
import { categoryVar, day, dayAndTime, money, number } from "../format";
import type { Basket, BasketDetail, Stats } from "../types";

/**
 * Two years of shopping as one continuous ruled roll. See DESIGN.md.
 *
 * Not a list of cards. Your March baskets are not a separate concern from your
 * April baskets, and putting each one in its own floating tile said they were.
 * Hairline rules, the month printed once at the moment it changes, line items
 * unfurling in place rather than into a collapsing panel, and the page
 * reference sitting in the margin where a citation belongs.
 */
export function Timeline({
  requestId,
  arrival = null,
  onClearArrival,
}: {
  requestId: number;
  /** The product this view was opened *for*, from the URL. `query` is what the
   *  search actually matches — a UPC when the Products index sent it, because
   *  the search is a substring match and product names contain each other —
   *  and `label` is the human name to say it with. */
  arrival?: { query: string; label: string } | null;
  onClearArrival?: () => void;
}) {
  const [store, setStore] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [q, setQ] = useState(arrival?.query ?? "");
  const [open, setOpen] = useState<number | null>(null);

  const timeline = useAsync(
    () => api.timeline(requestId, { store, date_from: from, date_to: to, q }),
    [requestId, store, from, to, q],
  );

  if (timeline.error) return <ErrorBox error={timeline.error} />;
  if (!timeline.data) return <Spinner label="Reading the timeline" />;

  const { stats, baskets, filtered_count } = timeline.data;
  return (
    <TimelineBody
      stats={stats}
      baskets={baskets}
      filteredCount={filtered_count}
      controls={{ store, setStore, from, setFrom, to, setTo, q, setQ }}
      open={open}
      setOpen={setOpen}
      // Only while the filter is still the one you arrived with. Edit the
      // search box and the sentence stops being true, so it goes away.
      arrival={arrival && q === arrival.query ? arrival : null}
      indexHref={`?tab=products&r=${requestId}`}
      onClearArrival={() => {
        setQ("");
        onClearArrival?.();
      }}
    />
  );
}

type Controls = {
  store: string;
  setStore: (v: string) => void;
  from: string;
  setFrom: (v: string) => void;
  to: string;
  setTo: (v: string) => void;
  q: string;
  setQ: (v: string) => void;
};

/** One cent. Below that a difference is float noise from summing currency. */
const FOOTING_TOLERANCE = 0.01;

/**
 * Do this basket's line items add up to the total the retailer stated for it?
 *
 * Shared by the summary note and the row marker on purpose. They disagreed
 * once, and a count in a header that does not match the rows marked below it is
 * worse than not marking anything.
 */
export function doesNotFoot(basket: Basket): boolean {
  const delta = basket.stated_pre_discount_delta;
  return delta !== null && Math.abs(delta) >= FOOTING_TOLERANCE;
}

function TimelineBody({
  stats,
  baskets,
  filteredCount,
  controls,
  open,
  setOpen,
  arrival,
  indexHref,
  onClearArrival,
}: {
  stats: Stats;
  baskets: Basket[];
  filteredCount: number;
  controls: Controls;
  open: number | null;
  setOpen: (v: number | null) => void;
  arrival: { query: string; label: string } | null;
  indexHref: string;
  onClearArrival: () => void;
}) {
  const { store, setStore, from, setFrom, to, setTo, q, setQ } = controls;
  const { visible, control, revealThrough } = useShowMore(baskets, 25);
  const months = monthIndex(baskets);
  const currentMonth = useCurrentMonth(months, visible.length);
  const jumpTo = useMonthJump(revealThrough);

  // A retailer that answered with a letter disclosed no purchases at all. The
  // stat row used to render that as Visits 0 / Total spend $0.00, which reads
  // as a fact about the retailer rather than the silence it actually was.
  if (!stats.disclosed) {
    return (
      <Spine margin={<Aside>no purchase data</Aside>}>
        <h2 className="font-serif text-[17px] font-semibold">Nothing to show here</h2>
        <p className="mt-2 max-w-[62ch] text-muted">
          This response contained no purchase data, so there is nothing to plot.
          That is not the same as having made no purchases: the retailer did not
          disclose the specific pieces of personal information it holds.
        </p>
        <p className="mt-2 max-w-[62ch] text-muted">
          The absence is recorded as a finding in the <strong>Compliance</strong>{" "}
          view, which is where a response like this is worth reading.
        </p>
      </Spine>
    );
  }

  let lastMonth = "";

  return (
    <div className="space-y-8">
      <Spine margin={<StoreKey stats={stats} />}>
        <Header stats={stats} />
        <FootingNote baskets={baskets} />
      </Spine>

      {/* Below lg the margin does not exist, so the months come inline as the
          chart they always were — now clickable, which is the half the rail
          adds. Above lg the rail carries them and this 148px comes back. */}
      <div className="lg:hidden">
        <Spine>
          <MonthChart months={months} current={currentMonth} onJump={jumpTo} />
        </Spine>
      </div>

      <Spine
        margin={
          <Aside>
            {number(filteredCount)}
            {filteredCount !== stats.basket_count
              ? ` of ${number(stats.basket_count)}`
              : ""}{" "}
            visits
          </Aside>
        }
      >
        <Filters
          stats={stats}
          store={store}
          setStore={setStore}
          from={from}
          setFrom={setFrom}
          to={to}
          setTo={setTo}
          q={q}
          setQ={setQ}
        />
      </Spine>

      {arrival && (
        <Spine margin={<Aside>filtered</Aside>}>
          <Arrival
            product={arrival.label}
            visits={filteredCount}
            total={stats.basket_count}
            indexHref={indexHref}
            onClear={onClearArrival}
          />
        </Spine>
      )}

      {baskets.length === 0 ? (
        <Spine>
          <Empty>No visits match those filters.</Empty>
        </Spine>
      ) : (
        <Spine
          marginFirst
          margin={
            <MonthRail months={months} current={currentMonth} onJump={jumpTo} />
          }
        >
          <RunningHead
            months={months}
            current={currentMonth}
            shown={visible.length}
            total={baskets.length}
          />
          <div className="border-t border-rule">
            {visible.map((basket) => {
              const month = basket.occurred_at.slice(0, 7);
              // Printed once, at the moment it changes, and never repeated.
              const first = month !== lastMonth;
              const label = first ? monthLabel(basket.occurred_at) : "";
              lastMonth = month;
              return (
                <BasketRow
                  key={basket.id}
                  basket={basket}
                  month={label}
                  // The anchor the rail jumps to and the running head reads.
                  // On the row rather than on a separate marker element: a
                  // zero-height marker between grid children would be a grid
                  // child too, and would take a row of its own.
                  monthKey={first ? month : null}
                  open={open === basket.id}
                  onToggle={() => setOpen(open === basket.id ? null : basket.id)}
                />
              );
            })}
            {control}
          </div>
        </Spine>
      )}
    </div>
  );
}

/**
 * Why this roll is short.
 *
 * Arriving from the Products index lands the reader partway into a document
 * that is quietly showing 6 rows instead of 121, with the cause sitting in a
 * search field they never typed into and may have already scrolled past. A
 * short list with no stated reason reads as missing data, not as a filter.
 *
 * Says it in the page's own voice rather than as a chip or a pill, and carries
 * both ways out: clear the filter, or go back to the index.
 */
function Arrival({
  product,
  visits,
  total,
  indexHref,
  onClear,
}: {
  product: string;
  visits: number;
  /** Null when the retailer disclosed no count to compare against. The
   *  sentence drops the comparison rather than inventing a denominator. */
  total: number | null;
  indexHref: string;
  onClear: () => void;
}) {
  return (
    <p className="max-w-[62ch] border-l-2 border-rule pl-3 font-serif text-[15px] leading-relaxed">
      Showing {number(visits)}
      {total === null ? "" : ` of ${number(total)}`} visits, the ones that
      included <span className="num text-[13px]">{product}</span>.{" "}
      <button
        onClick={onClear}
        className="text-accent underline underline-offset-2 hover:no-underline"
      >
        Show every visit
      </button>{" "}
      <span className="text-faint">or</span>{" "}
      <a
        href={indexHref}
        className="text-accent underline underline-offset-2 hover:no-underline"
      >
        back to the index
      </a>
      .
    </p>
  );
}

/** The stores, in the margin, each with the hue its rows carry below. */
function StoreKey({ stats }: { stats: Stats }) {
  if (stats.stores.length === 0) return <Aside>no store recorded</Aside>;
  return (
    <div className="num pt-2 text-[11.5px] text-faint">
      {stats.stores.map((s) => (
        <div key={s.store_code} className="flex items-baseline gap-2">
          <span
            aria-hidden
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ background: categoryVar(s.store_code) }}
          />
          <span>
            {s.store_code} · {s.visits}
          </span>
        </div>
      ))}
    </div>
  );
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function monthLabel(iso: string) {
  const [y, m] = iso.slice(0, 7).split("-");
  return `${MONTHS[Number(m) - 1]} ${y.slice(2)}`;
}

export type MonthEntry = {
  /** `YYYY-MM`, and the anchor id is `month-${key}`. */
  key: string;
  label: string;
  paid: number;
  saved: number;
  visits: number;
  /** Index into the *unsliced* basket list of the first visit in this month.
   *  The rail reveals through this before scrolling, because the roll renders
   *  25 rows at a time and an anchor that is not mounted scrolls nowhere. */
  firstIndex: number;
};

/**
 * The roll's months, in order, with what each cost and where each begins.
 *
 * One pass over the baskets rather than a group-then-sort, because the API
 * already returns them ordered by `occurred_at` and re-sorting would invent an
 * order the rows do not have. `firstIndex` is captured on the month's first
 * sighting, which is what makes it an index into the list as rendered.
 */
export function monthIndex(baskets: Basket[]): MonthEntry[] {
  const out: MonthEntry[] = [];
  const seen = new Map<string, MonthEntry>();
  baskets.forEach((basket, index) => {
    const key = basket.occurred_at.slice(0, 7);
    let entry = seen.get(key);
    if (!entry) {
      entry = {
        key,
        label: monthLabel(basket.occurred_at),
        paid: 0,
        saved: 0,
        visits: 0,
        firstIndex: index,
      };
      seen.set(key, entry);
      out.push(entry);
    }
    entry.paid += basket.paid_total ?? 0;
    entry.saved += basket.saved_total ?? 0;
    entry.visits += 1;
  });
  return out;
}

/**
 * Header figures. Serif numerals, sans labels.
 *
 * "Total paid" is the question people arrive with, so it is set larger than the
 * rest. Five identically weighted cards gave the answer no more prominence than
 * the line count.
 */
function Header({ stats }: { stats: Stats }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-8 gap-y-5">
      <div>
        <div className="text-[11.5px] tracking-[0.07em] text-muted uppercase">
          Total paid
        </div>
        <div className="font-serif text-[34px] leading-none font-semibold tabular-nums">
          {money(stats.total_paid)}
        </div>
        {stats.total_saved ? (
          <div className="num mt-1 text-[11.5px] text-faint">
            {money(stats.total_shelf)} shelf · {money(stats.total_saved)} saved
          </div>
        ) : null}
      </div>
      <Figure label="Visits" value={number(stats.basket_count)} />
      <Figure
        label="Products"
        value={number(stats.distinct_products)}
        note={
          stats.zero_value_lines
            ? `${number(stats.zero_value_lines)} lines name none`
            : undefined
        }
      />
      <Figure label="Line items" value={number(stats.line_count)} />
      <Figure
        label="Covered"
        value={`${day(stats.first_visit)} → ${day(stats.last_visit)}`}
        small
      />
    </div>
  );
}

function Figure({
  label,
  value,
  note,
  small,
}: {
  label: string;
  value: string;
  note?: string;
  small?: boolean;
}) {
  return (
    <div>
      <div className="text-[11.5px] tracking-[0.07em] text-muted uppercase">{label}</div>
      <div
        className={
          small
            ? "num text-[13px] leading-none font-semibold"
            : "font-serif text-[22px] leading-none font-semibold tabular-nums"
        }
      >
        {value}
      </div>
      {note && <div className="num mt-1 text-[11.5px] text-faint">{note}</div>}
    </div>
  );
}

/**
 * Baskets whose lines disagree with the retailer's own stated total.
 *
 * Hand-checked against the source: the difference is in the response as
 * supplied, so this is a finding about the disclosure rather than a fault to
 * fix. It stays because a basket that will not add up by hand is otherwise
 * indistinguishable from a reading error, and the reader blames the tool.
 */
function FootingNote({ baskets }: { baskets: Basket[] }) {
  const off = baskets.filter(doesNotFoot);
  if (off.length === 0) return null;
  const worst = Math.max(...off.map((b) => Math.abs(b.stated_pre_discount_delta ?? 0)));
  return (
    <p className="mt-5 max-w-[62ch] text-muted">
      {number(off.length)} of {number(baskets.length)} baskets do not add up to the
      shelf total the retailer stated for them, the largest by {money(worst)}. They
      are marked below. The difference is in the response as it arrived, not in how
      it was read, so there is nothing here to correct.
    </p>
  );
}

/**
 * The running head, which now runs.
 *
 * The month used to print once at the moment it changed and then scroll away,
 * so from about row 40 of a ~5,300px roll nothing on screen answered "when am
 * I". A book solves this with a running head at the top of every page; this is
 * the same device on a surface that has one very long page.
 *
 * One line, and only what a running head carries: where you are and how much of
 * the roll is on the page. The filter row is deliberately not pinned with it —
 * at 320px those four controls wrap to about 150px of sticky furniture, which
 * is half a phone screen spent on a set-and-forget control, and the rail
 * already carries the navigation that made pinning them attractive.
 */
function RunningHead({
  months,
  current,
  shown,
  total,
}: {
  months: MonthEntry[];
  current: string | null;
  shown: number;
  total: number;
}) {
  const here = months.find((m) => m.key === current);
  return (
    <div className="num sticky top-0 z-10 flex items-baseline justify-between gap-3 border-b border-rule bg-page py-2 text-[11.5px] text-faint">
      {/* Empty above the first month, not an em-rule. A running head is omitted
          on the opening page of a chapter; printing a placeholder there reads as
          a value that failed to load, and the first row two lines below is
          already carrying its own month label. */}
      <span className="font-semibold text-ink">{here ? here.label : ""}</span>
      <span>
        {shown === total
          ? `${number(total)} visits`
          : `${number(shown)} of ${number(total)} visits`}
      </span>
    </div>
  );
}

/**
 * Which month the reader is inside, given where each month's mark currently is.
 *
 * Pure so it can be tested: the rule is "the last mark that has passed the
 * threshold", not "the first mark still on screen". Those differ exactly when a
 * month is taller than the viewport, which is most of them — with the second
 * rule a long month reports nothing and the running head goes blank in the
 * middle of the month it should be naming.
 */
export function currentMonthKey(
  marks: { key: string; top: number }[],
  threshold = 72,
): string | null {
  let current: string | null = null;
  for (const mark of marks) {
    if (mark.top > threshold) break;
    current = mark.key;
  }
  return current;
}

/** Track the month under the running head. Re-reads when more rows appear. */
function useCurrentMonth(months: MonthEntry[], renderedCount: number) {
  const [current, setCurrent] = useState<string | null>(null);
  useEffect(() => {
    const read = () =>
      setCurrent(
        currentMonthKey(
          months
            .map((m) => ({ key: m.key, el: document.getElementById(`month-${m.key}`) }))
            .filter((m): m is { key: string; el: HTMLElement } => m.el !== null)
            .map((m) => ({ key: m.key, top: m.el.getBoundingClientRect().top })),
        ),
      );
    read();
    window.addEventListener("scroll", read, { passive: true });
    window.addEventListener("resize", read);
    return () => {
      window.removeEventListener("scroll", read);
      window.removeEventListener("resize", read);
    };
  }, [months, renderedCount]);
  return current;
}

/**
 * Reveal the target row if it is not rendered yet, then scroll to it.
 *
 * The scroll cannot simply wait for the next render. `revealThrough` is a
 * `setState` that returns the same limit when the row is already revealed, and
 * React bails out of re-rendering on an unchanged value — so the second jump
 * onward produced no render, the effect never ran, and the rail silently did
 * nothing. Measured before the fix: the first click revealed 25 rows to 123 and
 * scrolled; every click after it left the page exactly where it was.
 *
 * So the common case scrolls immediately, and only a jump that genuinely needs
 * more rows waits for them to mount.
 */
function useMonthJump(revealThrough: (index: number) => void) {
  const pending = useRef<string | null>(null);
  useEffect(() => {
    if (!pending.current) return;
    const el = document.getElementById(`month-${pending.current}`);
    if (!el) return; // still not mounted; try again on the next render
    pending.current = null;
    scrollToMonth(el);
  });
  return (month: MonthEntry) => {
    revealThrough(month.firstIndex);
    const el = document.getElementById(`month-${month.key}`);
    if (el) {
      scrollToMonth(el);
      return;
    }
    pending.current = month.key;
  };
}

/** `auto`, not `smooth`: DESIGN.md allows exactly one motion in this app and
 *  this is not it. A jump that animates across 5,000px is also worse at
 *  answering "where am I now" than one that is simply already there. */
function scrollToMonth(el: HTMLElement) {
  el.scrollIntoView({ behavior: "auto", block: "start" });
}

/**
 * The months, rotated into the margin as a rail you can steer with.
 *
 * The inline chart this replaces was 148px of picture you could not act on, and
 * it scrolled away after the first screen. Turned ninety degrees it becomes the
 * one thing the margin was short of: a way to answer "when am I, and take me
 * somewhere else" from any point in a roll measured at ~5,300px.
 *
 * Bar length is quantity, which is one of the three things colour and length are
 * allowed to mean here. The current month is marked with weight and the accent,
 * which is interaction, not status.
 */
function MonthRail({
  months,
  current,
  onJump,
}: {
  months: MonthEntry[];
  current: string | null;
  onJump: (month: MonthEntry) => void;
}) {
  if (months.length === 0) return null;
  const peak = Math.max(...months.map((m) => m.paid + m.saved), 1);
  return (
    <div className="sticky top-4 pt-2">
      <div className="num border-b border-rule pb-1 text-[11.5px] text-faint">
        paid, by month
      </div>
      <ol className="mt-2">
        {months.map((month) => {
          const here = month.key === current;
          return (
            <li key={month.key}>
              {/* A link in a list, not a button. `@media (pointer: coarse)`
                  puts a 44px floor under every button and exempts `a` inside
                  `li`; at two years of months that is the difference between a
                  rail that fits the margin and one over 1,000px tall on a
                  tablet, where `sticky` then has nothing to stick. Same
                  reasoning as the product index, and a real href is the right
                  semantics for something that navigates. */}
              <a
                href={`#month-${month.key}`}
                onClick={(e) => {
                  // The href alone would scroll to a row the roll may not have
                  // rendered yet, so the handler reveals first and then jumps.
                  e.preventDefault();
                  onJump(month);
                }}
                aria-current={here ? "true" : undefined}
                title={`${month.label} · ${money(month.paid)} paid · ${number(
                  month.visits,
                )} visits`}
                className={`group flex w-full items-center gap-2 py-[3px] text-left ${
                  here ? "text-accent" : "text-faint hover:text-ink"
                }`}
              >
                <span
                  className={`num w-[3.25rem] shrink-0 text-[11.5px] ${
                    here ? "font-semibold" : ""
                  }`}
                >
                  {month.label}
                </span>
                <span aria-hidden className="flex h-[7px] min-w-0 flex-1 items-stretch">
                  <span
                    className={here ? "bg-accent" : "bg-ink/55 group-hover:bg-ink"}
                    style={{ width: `${(month.paid / peak) * 100}%` }}
                  />
                  <span
                    className="bg-line/45"
                    style={{ width: `${(month.saved / peak) * 100}%` }}
                  />
                </span>
              </a>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/**
 * Spend by month, below `lg`, where the margin and its rail do not exist.
 *
 * Hand-rolled rather than a chart library: the design calls for no frame, no
 * gridlines, no axis box and no legend, and fighting a charting library out of
 * its chrome is more work than a row of divs. It also removes a mount animation
 * that had to be suppressed for reduced-motion.
 *
 * Paid, not shelf. Plotting the pre-discount sum drew a spending history nobody
 * had: every bar stood taller than the month actually cost.
 *
 * Each bar is a button now. The picture was the only thing on this view that
 * showed the whole two years at once and the only thing you could not act on.
 */
function MonthChart({
  months,
  current,
  onJump,
}: {
  months: MonthEntry[];
  current: string | null;
  onJump: (month: MonthEntry) => void;
}) {
  if (months.length === 0) return <Empty>Nothing to plot.</Empty>;
  const peak = Math.max(...months.map((m) => m.paid + m.saved), 1);
  return (
    <div>
      <div className="num pb-1 text-[11.5px] text-faint">paid, by month</div>
      <div className="flex h-24 items-end gap-[3px] border-b border-rule">
        {months.map((m) => (
          <button
            key={m.key}
            onClick={() => onJump(m)}
            aria-current={m.key === current ? "true" : undefined}
            aria-label={`Jump to ${m.label}`}
            className="flex h-full flex-1 flex-col justify-end"
            title={`${m.label} · ${money(m.paid)} paid${
              m.saved > 0 ? `, ${money(m.saved)} saved` : ""
            } · ${number(m.visits)} visits`}
          >
            {/* The saving sits above the paid amount, so the full bar height is
                the shelf total. Both readings the response supports, one mark. */}
            <span
              className="block bg-line/45"
              style={{ height: `${(m.saved / peak) * 92}px` }}
            />
            <span
              className={`block ${m.key === current ? "bg-accent" : "bg-ink/75"}`}
              style={{ height: `${(m.paid / peak) * 92}px` }}
            />
          </button>
        ))}
      </div>
      <div className="num flex justify-between pt-1.5 text-[11.5px] text-faint">
        <span>{months[0].key}</span>
        {months.length > 2 && <span>{months[Math.floor(months.length / 2)].key}</span>}
        <span>{months[months.length - 1].key}</span>
      </div>
    </div>
  );
}

function Filters({
  stats,
  store,
  setStore,
  from,
  setFrom,
  to,
  setTo,
  q,
  setQ,
}: { stats: Stats } & Controls) {
  // Visible labels on every control. A placeholder is not a label, and it
  // disappears the moment you type; nothing said the two dates were a range.
  const field =
    "rounded-[2px] border border-line bg-transparent px-2 py-1.5 text-ink " +
    "focus:border-accent focus:outline-2 focus:outline-offset-1 focus:outline-accent";
  const legend =
    "flex flex-col gap-1 text-[11.5px] tracking-[0.05em] text-muted uppercase";
  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className={legend}>
        Search
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="product or UPC"
          className={`${field} w-44 normal-case`}
        />
      </label>
      <label className={legend}>
        Store
        <select
          value={store}
          onChange={(e) => setStore(e.target.value)}
          className={`${field} normal-case`}
        >
          <option value="">every store</option>
          {stats.stores.map((s) => (
            <option key={s.store_code} value={s.store_code}>
              {s.store_code} ({s.visits})
            </option>
          ))}
        </select>
      </label>
      <label className={legend}>
        From
        <input
          type="date"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
          className={`${field} num`}
        />
      </label>
      <label className={legend}>
        To
        <input
          type="date"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          className={`${field} num`}
        />
      </label>
    </div>
  );
}

function BasketRow({
  basket,
  month,
  monthKey,
  open,
  onToggle,
}: {
  basket: Basket;
  month: string;
  /** Set on the first row of each month; the id the rail scrolls to. */
  monthKey: string | null;
  open: boolean;
  onToggle: () => void;
}) {
  const detail = useAsync<BasketDetail | null>(
    () => (open ? api.transaction(basket.id) : Promise.resolve(null)),
    [open, basket.id],
  );
  const unreconciled = doesNotFoot(basket);

  return (
    <div
      id={monthKey ? `month-${monthKey}` : undefined}
      // Clears the running head, which is sticky and would otherwise cover the
      // first row of the month a jump just landed on.
      className="grid scroll-mt-12 grid-cols-[3.25rem_minmax(0,1fr)] gap-x-4 lg:scroll-mt-14 lg:grid-cols-[3.25rem_minmax(0,1fr)] lg:gap-x-12">
      {/* The month, printed once, hanging in its own column. */}
      <div className="font-serif text-[12.5px] text-faint">
        {month && <span className="block pt-2.5">{month}</span>}
      </div>

      <div className="min-w-0 border-b border-rule">
        <button
          onClick={onToggle}
          aria-expanded={open}
          // Wraps to two lines when the row cannot hold one. Measured at 375px
          // on a single line, the store column was squeezed to 0px and the page
          // scrolled sideways: 160px of that row is two fixed-width money
          // columns, so the only flexible thing on it absorbed the entire
          // shortfall. What you bought where is the point of the row, so the
          // amounts move to their own line rather than the store disappearing.
          className={`flex w-full flex-wrap items-baseline gap-x-2 gap-y-1 py-2.5 text-left md:gap-x-3 ${
            // Marked, not tinted. A fifth of these rows carry it, and tinting a
            // fifth of a long list makes the list look broken rather than
            // making the rows findable. Quiet on purpose: the difference is the
            // retailer's, and there is nothing for the reader to fix.
            unreconciled ? "border-l-2 border-dotted border-line pl-2" : ""
          }`}
        >
          <span
            aria-hidden
            // text-line clears the 3:1 floor for a UI component. It is the only
            // thing showing open/closed to a sighted user.
            className={`shrink-0 select-none text-line ${open ? "rotate-90" : ""}`}
          >
            ›
          </span>
          <span className="num shrink-0 text-[12.5px]">
            {dayAndTime(basket.occurred_at).slice(8)}
          </span>
          <span className="flex min-w-0 flex-1 items-baseline gap-2 text-muted">
            {basket.store_code && (
              <span
                aria-hidden
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ background: categoryVar(basket.store_code) }}
                title={`store ${basket.store_code}`}
              />
            )}
            {/* Where and how you paid. The item count used to live here too,
                which made this the longest string on the row and the thing that
                got clipped: at 375px "store 00318 · GIFT CARD · 22 items" wants
                215px and has 151. It is a quantity, so it sits with the other
                quantities now. `truncate` stays as the guard for a tender
                longer than any this format has produced. */}
            <span className="truncate">
              store {basket.store_code ?? "—"}
              {basket.tender_type ? ` · ${basket.tender_type}` : ""}
            </span>
          </span>

          {/* Below `md` the amounts take a line of their own: `basis-full`
              rather than a shrink hint, because the store column carries
              `min-w-0` for its truncation and will therefore collapse to zero
              before a shrink-driven wrap ever triggers. That is exactly how it
              reached 0px. `flex-wrap` inside handles the widest case, an
              unreconciled row whose "over by" note joins the two amounts.

              `md`, not `sm`. Measured across the fixture: at 640px the one-line
              row leaves the store column 75px against the 153px the longest
              tender needs, and all 25 rows clip; at 700px, 8 still clip; at
              768px, none do. The breakpoint is where the content fits, not
              where the default scale happens to put it. */}
          <span className="flex basis-full flex-wrap items-baseline justify-end gap-x-3 gap-y-1 md:ml-auto md:basis-auto md:flex-nowrap">
            {unreconciled && (
              <span
                className="shrink-0 text-muted underline decoration-dotted underline-offset-2"
                title="These line items do not add up to the total the retailer stated for this basket. The difference is in the response as supplied, not in how it was read."
              >
                {(basket.stated_pre_discount_delta ?? 0) > 0 ? "over" : "under"} by{" "}
                {money(Math.abs(basket.stated_pre_discount_delta ?? 0))}
              </span>
            )}
            <span className="num shrink-0 text-muted">
              {number(basket.item_count)} items
            </span>
            {/* w-16 below sm: the widest amount here is about 53px of Iosevka,
                so 80px was reserving space this row cannot spare on a phone. */}
            <span className="num w-16 shrink-0 text-right text-muted md:w-20">
              {basket.saved_total > 0 ? `−${money(basket.saved_total)}` : ""}
            </span>
            <span className="num w-16 shrink-0 text-right font-semibold md:w-20">
              {money(basket.paid_total)}
            </span>
            {/* The citation rides the row at every width now. The margin
                beside this roll holds the month rail, and a sticky rail and a
                per-row footnote cannot share one column — the rows would scroll
                underneath the rail and disappear behind it. Below lg this was
                already the shipped behaviour, so the view is now consistent
                across widths rather than carrying two patterns. Recorded in
                DESIGN.md's decisions log, which is where a departure from
                "footnotes belong in the margin" has to be written down. */}
            <Cite provenance={basket.provenance} />
          </span>
        </button>

        {open && (
          <div className="pb-4">
            {detail.loading && <Spinner label="Opening the basket" />}
            {detail.data && <LineItems detail={detail.data} />}
          </div>
        )}
      </div>

    </div>
  );
}

/**
 * The receipt, unfurled in place.
 *
 * `loyalty_amt` is the price the line cost, not a discount to subtract, so what
 * you paid is that amount and the saving is the difference. The footer foots:
 * the bold figure on the row above is these lines added up, and before this it
 * was not.
 */
function LineItems({ detail }: { detail: BasketDetail }) {
  const stated = detail.total_pre_discount;
  const delta = detail.stated_pre_discount_delta;
  return (
    <div className="scroll-x">
      <table className="w-full min-w-[34rem] text-[12.5px]">
        <thead className="text-[11.5px] tracking-[0.05em] text-muted uppercase">
          <tr className="text-left">
            <th className="py-1.5 font-medium">Description</th>
            <th className="py-1.5 font-medium">UPC</th>
            <th className="py-1.5 text-right font-medium">Shelf</th>
            <th className="py-1.5 text-right font-medium">You paid</th>
            <th className="py-1.5 text-right font-medium">Saved</th>
          </tr>
        </thead>
        <tbody>
          {detail.items.map((item) => {
            // A row naming no product at zero cost is a placeholder in the
            // export, not something you bought. Shown, but marked.
            const placeholder =
              item.description_raw === "UNKNOWN" && (item.retail_amt ?? 0) === 0;
            return (
              <tr
                key={item.id}
                className={`border-t border-rule ${placeholder ? "text-faint" : ""}`}
              >
                <td className="py-1.5 pr-3">
                  {item.description_raw || "(blank)"}
                  {placeholder && (
                    <span
                      className="ml-1.5 italic"
                      title="A placeholder row in the retailer's export — no product, no amount."
                    >
                      placeholder
                    </span>
                  )}
                </td>
                <td className="num py-1.5 pr-3 text-[11.5px] text-faint">
                  {item.upc ?? "—"}
                </td>
                <td className="num py-1.5 text-right">{money(item.retail_amt)}</td>
                <td className="num py-1.5 text-right font-semibold">
                  {money(item.paid_amt)}
                </td>
                {/* A dash, not $0.00, when the line was full price. Most lines
                    are, and a column of zeros reads as broken. */}
                <td className="num py-1.5 text-right text-muted">
                  {item.saved_amt ? `−${money(item.saved_amt)}` : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot className="border-t border-line font-semibold">
          <tr>
            <td className="py-1.5" colSpan={2}>
              {number(detail.item_count)} lines
            </td>
            <td className="num py-1.5 text-right">{money(detail.shelf_total)}</td>
            <td className="num py-1.5 text-right">{money(detail.paid_total)}</td>
            <td className="num py-1.5 text-right">
              {detail.saved_total ? `−${money(detail.saved_total)}` : "—"}
            </td>
          </tr>
        </tfoot>
      </table>

      {/* The retailer's own arithmetic, checked rather than assumed. Silent
          while it agrees; the disagreement is the whole reason to show it. */}
      {stated !== null && (
        <p className="mt-2 max-w-[62ch] text-[11.5px] text-muted">
          {delta !== null && Math.abs(delta) >= FOOTING_TOLERANCE ? (
            <>
              These lines add up to {money(detail.shelf_total)} before discounts; the
              retailer states {money(stated)}, a difference of {money(Math.abs(delta))}.
              Both figures come from the response as supplied and it does not
              reconcile them.
            </>
          ) : (
            <>
              Matches the {money(stated)} the retailer states for this basket, before
              discounts.
            </>
          )}
        </p>
      )}
    </div>
  );
}
