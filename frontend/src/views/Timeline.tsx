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

      {stats.negative_lines > 0 && (
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
          <div className="flex flex-wrap gap-2">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="product or UPC"
              className="w-40 rounded border border-stone-300 px-2 py-1 text-xs dark:border-stone-700 dark:bg-stone-950"
            />
            <select
              value={store}
              onChange={(e) => setStore(e.target.value)}
              className="rounded border border-stone-300 px-2 py-1 text-xs dark:border-stone-700 dark:bg-stone-950"
            >
              <option value="">every store</option>
              {stats.stores.map((s) => (
                <option key={s.store_code} value={s.store_code}>
                  {s.store_code} ({s.visits})
                </option>
              ))}
            </select>
            <input
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              className="rounded border border-stone-300 px-2 py-1 text-xs dark:border-stone-700 dark:bg-stone-950"
            />
            <input
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="rounded border border-stone-300 px-2 py-1 text-xs dark:border-stone-700 dark:bg-stone-950"
            />
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
    <li className="py-2">
      <button
        onClick={onToggle}
        className="flex w-full items-baseline justify-between gap-3 text-left text-sm hover:opacity-80"
      >
        <span className="tabular-nums">{dayAndTime(basket.occurred_at)}</span>
        <span className="text-stone-500 dark:text-stone-400">
          store {basket.store_code ?? "—"}
          {basket.tender_type ? ` · ${basket.tender_type}` : ""}
        </span>
        <span className="ml-auto tabular-nums">{number(basket.item_count)} items</span>
        <span className="w-20 text-right font-medium tabular-nums">
          {money(basket.items_total)}
        </span>
        <ProvenanceTag provenance={basket.provenance} />
      </button>

      {open && (
        <div className="mt-2 scroll-x">
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
