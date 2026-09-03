import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import { useAsync } from "../components/useAsync";
import { Card, Empty, ErrorBox, ProvenanceTag, Spinner, Stat } from "../components/ui";
import { useShowMore } from "../components/ShowMore";
import { day, dayAndTime, money, number } from "../format";
import type { Basket, BasketDetail, Stats } from "../types";

export function Timeline({ requestId }: { requestId: number }) {
  const [store, setStore] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [q, setQ] = useState("");
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

function TimelineBody({
  stats,
  baskets,
  filteredCount,
  controls,
  open,
  setOpen,
}: {
  stats: Stats;
  baskets: Basket[];
  filteredCount: number;
  controls: Controls;
  open: number | null;
  setOpen: (v: number | null) => void;
}) {
  const { store, setStore, from, setFrom, to, setTo, q, setQ } = controls;
  const { visible, control } = useShowMore(baskets, 25);

  // A retailer that answered with a letter disclosed no purchases at all. The
  // stat cards used to render that as Visits 0 / Total spend $0.00, which reads
  // as a fact about the retailer rather than the silence it actually was. Say
  // what happened instead of showing zeros.
  if (!stats.disclosed) {
    return (
      <Card title="No data to show">
        <p className="text-sm text-stone-700 dark:text-stone-300">
          This response contained no purchase data, so there is nothing to plot
          here. That is not the same as having made no purchases: the retailer
          did not disclose the specific pieces of personal information it holds.
        </p>
        <p className="mt-2 text-sm text-stone-600 dark:text-stone-400">
          The absence is recorded as a finding in the{" "}
          <strong>Compliance</strong> view, which is where a response like this
          is worth reading.
        </p>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Visits" value={number(stats.basket_count)} />
        <Stat label="Total spend" value={money(stats.total_spend)} />
        <Stat
          label="Distinct products"
          value={number(stats.distinct_products)}
          hint={
            stats.zero_value_lines
              ? `${number(stats.zero_value_lines)} lines name no product`
              : undefined
          }
        />
        <Stat label="Line items" value={number(stats.line_count)} />
        <Stat
          label="Covered"
          value={
            <span className="block text-sm leading-tight whitespace-nowrap">
              {day(stats.first_visit)}
              <br />→ {day(stats.last_visit)}
            </span>
          }
        />
      </div>

      {(stats.negative_lines ?? 0) > 0 && (
        <p className="text-xs text-stone-500 dark:text-stone-400">
          {number(stats.negative_lines)} lines carry a negative amount — returns and
          voids. They are kept, because filtering them would overstate what you spent.
        </p>
      )}

      <Card title="Visits over the coverage window">
        <VisitChart baskets={baskets} />
      </Card>

      <Card
        title={`Baskets (${number(filteredCount)}${
          filteredCount !== stats.basket_count ? ` of ${number(stats.basket_count)}` : ""
        })`}
        actions={
          // Every control here was labelled only by its placeholder, and the two
          // date inputs by nothing at all: a browser format hint is not a label,
          // and it disappears the moment you type. Nothing said the two dates
          // were a range. Visible labels, and py-1.5 for a slightly less
          // hostile touch target.
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-0.5 text-[11px] text-stone-500 dark:text-stone-400">
              Search
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="product or UPC"
                className="w-40 rounded border border-stone-300 px-2 py-1.5 text-xs text-stone-900 dark:border-stone-700 dark:bg-stone-950 dark:text-stone-100"
              />
            </label>
            <label className="flex flex-col gap-0.5 text-[11px] text-stone-500 dark:text-stone-400">
              Store
              <select
                value={store}
                onChange={(e) => setStore(e.target.value)}
                className="rounded border border-stone-300 px-2 py-1.5 text-xs text-stone-900 dark:border-stone-700 dark:bg-stone-950 dark:text-stone-100"
              >
                <option value="">every store</option>
                {stats.stores.map((s) => (
                  <option key={s.store_code} value={s.store_code}>
                    {s.store_code} ({s.visits})
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-0.5 text-[11px] text-stone-500 dark:text-stone-400">
              From
              <input
                type="date"
                value={from}
                onChange={(e) => setFrom(e.target.value)}
                className="rounded border border-stone-300 px-2 py-1.5 text-xs text-stone-900 dark:border-stone-700 dark:bg-stone-950 dark:text-stone-100"
              />
            </label>
            <label className="flex flex-col gap-0.5 text-[11px] text-stone-500 dark:text-stone-400">
              To
              <input
                type="date"
                value={to}
                onChange={(e) => setTo(e.target.value)}
                className="rounded border border-stone-300 px-2 py-1.5 text-xs text-stone-900 dark:border-stone-700 dark:bg-stone-950 dark:text-stone-100"
              />
            </label>
          </div>
        }
      >
        {baskets.length === 0 ? (
          <Empty>No visits match those filters.</Empty>
        ) : (
          <>
            <ul className="divide-y divide-stone-200 dark:divide-stone-800">
              {visible.map((basket) => (
                <BasketRow
                  key={basket.id}
                  basket={basket}
                  open={open === basket.id}
                  onToggle={() => setOpen(open === basket.id ? null : basket.id)}
                />
              ))}
            </ul>
            {control}
          </>
        )}
      </Card>
    </div>
  );
}

function VisitChart({ baskets }: { baskets: Basket[] }) {
  // One mark per visit, sized by what it cost. Monthly buckets keep two years of
  // shopping legible; the per-visit detail is one click away in the list below.
  const byMonth = new Map<string, { month: string; spend: number; visits: number }>();
  for (const basket of baskets) {
    const month = basket.occurred_at.slice(0, 7);
    const entry = byMonth.get(month) ?? { month, spend: 0, visits: 0 };
    entry.spend += basket.items_total ?? 0;
    entry.visits += 1;
    byMonth.set(month, entry);
  }
  const data = [...byMonth.values()];
  if (data.length === 0) return <Empty>Nothing to plot.</Empty>;

  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.12} />
          <XAxis dataKey="month" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 11 }} width={56} tickFormatter={(v) => money(v)} />
          <Tooltip
            formatter={(value: number, name) =>
              name === "spend" ? money(value) : number(value)
            }
            contentStyle={{ fontSize: 12 }}
          />
          <Bar dataKey="spend" fill="currentColor" className="text-stone-500" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function BasketRow({
  basket,
  open,
  onToggle,
}: {
  basket: Basket;
  open: boolean;
  onToggle: () => void;
}) {
  const detail = useAsync<BasketDetail | null>(
    () => (open ? api.transaction(basket.id) : Promise.resolve(null)),
    [open, basket.id],
  );

  return (
    <li className={open ? "py-1" : "py-0.5"}>
      <button
        onClick={onToggle}
        aria-expanded={open}
        // cursor-pointer and a disclosure caret, because nothing else said these
        // rows were clickable. Expanding a basket to its line items is the whole
        // point of this view, and the affordance was a hover opacity change that
        // does not exist on touch. py-1.5 also lifts the row from a 20px hit
        // target toward something usable on a phone.
        className={`flex w-full cursor-pointer items-start gap-2 rounded px-2 py-2 text-left text-sm transition hover:bg-stone-100 sm:items-baseline sm:gap-3 sm:py-1.5 dark:hover:bg-stone-800 ${
          open ? "bg-stone-100 dark:bg-stone-800" : ""
        }`}
      >
        <span
          aria-hidden
          // stone-500/400, not stone-400/500. The first version of this caret
          // landed at 2.59:1, reintroducing the exact contrast failure fixed in
          // FINDING-001 one commit earlier. aria-hidden makes it decorative to a
          // screen reader, but it is still the only thing showing open/closed to
          // everyone else, so it needs the 3:1 UI-component floor.
          className={`mt-0.5 shrink-0 select-none text-stone-500 transition-transform sm:mt-0 dark:text-stone-400 ${
            open ? "rotate-90" : ""
          }`}
        >
          ›
        </span>

        {/* Two deliberate lines on a phone, one row from `sm` up.
            Letting the five desktop columns wrap produced three or four ragged
            lines per basket, none of them aligned with the row above, so no
            column could be scanned. Here the date and the amount — the two
            things you actually scan a shopping history for — own the first
            line, and the descriptive detail drops to the second.

            `sm:contents` dissolves the two mobile line-wrappers at the
            breakpoint, so the desktop row stays a single flex line with exactly
            the columns it had before. */}
        <span className="flex min-w-0 flex-1 flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-3">
          <span className="flex items-baseline justify-between gap-3 sm:contents">
            <span className="tabular-nums">{dayAndTime(basket.occurred_at)}</span>
            <span className="font-medium tabular-nums sm:order-last sm:w-20 sm:text-right">
              {money(basket.items_total)}
            </span>
          </span>
          <span className="flex items-baseline justify-between gap-3 text-stone-500 sm:contents dark:text-stone-400">
            <span className="truncate">
              store {basket.store_code ?? "—"}
              {basket.tender_type ? ` · ${basket.tender_type}` : ""}
            </span>
            <span className="shrink-0 tabular-nums sm:ml-auto">
              {number(basket.item_count)} items
            </span>
          </span>
        </span>

        <ProvenanceTag provenance={basket.provenance} />
      </button>

      {open && (
        // Indented and rule-bordered so the line items read as detail belonging
        // to the row above, rather than a table injected into the list. Without
        // it the next basket row follows on the same divider weight and the
        // nesting is invisible.
        <div className="scroll-x ml-4 border-l-2 border-stone-200 pl-4 dark:border-stone-700">
          {detail.loading && <Spinner label="Opening the basket" />}
          {detail.data && <LineItems detail={detail.data} />}
        </div>
      )}
    </li>
  );
}

function LineItems({ detail }: { detail: BasketDetail }) {
  return (
    <table className="w-full min-w-[36rem] text-xs">
      <thead className="text-stone-500 dark:text-stone-400">
        <tr className="text-left">
          <th className="py-1 font-medium">Description</th>
          <th className="py-1 font-medium">UPC</th>
          <th className="py-1 text-right font-medium">Shelf</th>
          <th className="py-1 text-right font-medium">Loyalty</th>
          <th className="py-1 text-right font-medium">You paid</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
        {detail.items.map((item) => {
          // A row naming no product at zero cost is a placeholder in the export,
          // not something you bought. Shown, but marked.
          const placeholder =
            item.description_raw === "UNKNOWN" && (item.retail_amt ?? 0) === 0;
          return (
            <tr
              key={item.id}
              className={placeholder ? "text-stone-400 dark:text-stone-600" : ""}
            >
              <td className="py-1 pr-2">
                {item.description_raw || "(blank)"}
                {placeholder && (
                  <span
                    className="ml-1 text-[11px]"
                    title="A placeholder row in the retailer's export — no product, no amount."
                  >
                    placeholder
                  </span>
                )}
              </td>
              <td className="py-1 pr-2 font-mono text-[11px]">{item.upc ?? "—"}</td>
              <td className="py-1 text-right tabular-nums">{money(item.retail_amt)}</td>
              <td className="py-1 text-right tabular-nums">
                {item.loyalty_amt ? `−${money(item.loyalty_amt)}` : "—"}
              </td>
              <td className="py-1 text-right font-medium tabular-nums">
                {money(item.net_amt)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
