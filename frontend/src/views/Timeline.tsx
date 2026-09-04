import { useState } from "react";
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
  const { visible, control } = useShowMore(baskets, 25);

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

      <Spine margin={<Aside>paid, by month</Aside>}>
        <MonthChart baskets={baskets} />
      </Spine>

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
        <div className="border-t border-rule">
          {visible.map((basket) => {
            const month = basket.occurred_at.slice(0, 7);
            // Printed once, at the moment it changes, and never repeated.
            const label = month === lastMonth ? "" : monthLabel(basket.occurred_at);
            lastMonth = month;
            return (
              <BasketRow
                key={basket.id}
                basket={basket}
                month={label}
                open={open === basket.id}
                onToggle={() => setOpen(open === basket.id ? null : basket.id)}
              />
            );
          })}
          {control}
        </div>
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
 * Spend by month, drawn as bare ink bars on the roll's own baseline.
 *
 * Hand-rolled rather than a chart library: the design calls for no frame, no
 * gridlines, no axis box and no legend, and fighting a charting library out of
 * its chrome is more work than a row of divs. It also removes a mount animation
 * that had to be suppressed for reduced-motion.
 *
 * Paid, not shelf. Plotting the pre-discount sum drew a spending history nobody
 * had: every bar stood taller than the month actually cost.
 */
function MonthChart({ baskets }: { baskets: Basket[] }) {
  const byMonth = new Map<string, { month: string; paid: number; saved: number }>();
  for (const basket of baskets) {
    const month = basket.occurred_at.slice(0, 7);
    const entry = byMonth.get(month) ?? { month, paid: 0, saved: 0 };
    entry.paid += basket.paid_total ?? 0;
    entry.saved += basket.saved_total ?? 0;
    byMonth.set(month, entry);
  }
  const data = [...byMonth.values()].sort((a, b) => a.month.localeCompare(b.month));
  if (data.length === 0) return <Empty>Nothing to plot.</Empty>;

  const peak = Math.max(...data.map((m) => m.paid + m.saved), 1);
  return (
    <div>
      <div className="flex h-24 items-end gap-[3px] border-b border-rule">
        {data.map((m) => (
          <div
            key={m.month}
            className="flex-1"
            title={`${m.month} · ${money(m.paid)} paid${
              m.saved > 0 ? `, ${money(m.saved)} saved` : ""
            }`}
          >
            {/* The saving sits above the paid amount, so the full bar height is
                the shelf total. Both readings the response supports, one mark. */}
            <div className="bg-line/45" style={{ height: `${(m.saved / peak) * 92}px` }} />
            <div className="bg-ink/75" style={{ height: `${(m.paid / peak) * 92}px` }} />
          </div>
        ))}
      </div>
      <div className="num flex justify-between pt-1.5 text-[11.5px] text-faint">
        <span>{data[0].month}</span>
        {data.length > 2 && <span>{data[Math.floor(data.length / 2)].month}</span>}
        <span>{data[data.length - 1].month}</span>
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
  open,
  onToggle,
}: {
  basket: Basket;
  month: string;
  open: boolean;
  onToggle: () => void;
}) {
  const detail = useAsync<BasketDetail | null>(
    () => (open ? api.transaction(basket.id) : Promise.resolve(null)),
    [open, basket.id],
  );
  const unreconciled = doesNotFoot(basket);

  return (
    <div className="grid grid-cols-[3.25rem_minmax(0,1fr)] gap-x-4 lg:grid-cols-[3.25rem_minmax(0,var(--measure-read))_var(--spacing-margin)] lg:gap-x-12">
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
            {/* Below lg the margin collapses, so the citation comes inline. */}
            <span className="lg:hidden">
              <Cite provenance={basket.provenance} />
            </span>
          </span>
        </button>

        {open && (
          <div className="pb-4">
            {detail.loading && <Spinner label="Opening the basket" />}
            {detail.data && <LineItems detail={detail.data} />}
          </div>
        )}
      </div>

      <div className="hidden pt-2.5 lg:block">
        <Cite provenance={basket.provenance} />
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
