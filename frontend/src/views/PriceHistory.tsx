import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import { useAsync } from "../components/useAsync";
import { Card, Empty, ErrorBox, Pill, Spinner } from "../components/ui";
import { useShowMore } from "../components/ShowMore";
import { money, percent } from "../format";
import type { PriceSeries } from "../types";

/**
 * A personal inflation series, which the data contains for free: the same UPC
 * bought a dozen times over two years is a price history nobody had to collect.
 */
export function PriceHistory({ requestId }: { requestId: number }) {
  const [minObservations, setMinObservations] = useState(4);
  const [selected, setSelected] = useState<string | null>(null);
  const history = useAsync(
    () => api.priceHistory(requestId, minObservations),
    [requestId, minObservations],
  );

  if (history.error) return <ErrorBox error={history.error} />;
  if (!history.data) return <Spinner label="Building price histories" />;

  const { products, product_count } = history.data;
  const current = products.find((p) => p.upc === selected) ?? products[0];
  return (
    <PriceBody
      products={products}
      productCount={product_count}
      current={current}
      onSelect={setSelected}
      minObservations={minObservations}
      setMinObservations={setMinObservations}
    />
  );
}

function PriceBody({
  products,
  productCount,
  current,
  onSelect,
  minObservations,
  setMinObservations,
}: {
  products: PriceSeries[];
  productCount: number;
  current: PriceSeries | undefined;
  onSelect: (upc: string) => void;
  minObservations: number;
  setMinObservations: (n: number) => void;
}) {
  const { visible, control } = useShowMore(products, 30);

  if (products.length === 0) {
    return (
      <Empty>
        No product was bought often enough to show a price history. Try lowering the
        threshold.
      </Empty>
    );
  }

  return (
    <div className="space-y-4">
      <Card
        title={`${productCount} products with a price history`}
        actions={
          <label className="flex items-center gap-2 text-xs text-stone-500 dark:text-stone-400">
            seen at least
            <input
              type="number"
              min={2}
              max={40}
              value={minObservations}
              onChange={(e) => setMinObservations(Number(e.target.value) || 2)}
              className="w-14 rounded border border-stone-300 px-2 py-1 dark:border-stone-700 dark:bg-stone-950"
            />
            times
          </label>
        }
      >
        {current && <Series series={current} />}
      </Card>

      <Card title="Every product, by how often you bought it">
        <div className="scroll-x">
          <table className="w-full min-w-[42rem] text-xs">
            <thead className="text-stone-500 dark:text-stone-400">
              <tr className="text-left">
                <th className="py-1 font-medium">Product</th>
                <th className="py-1 text-right font-medium">Bought</th>
                <th className="py-1 text-right font-medium">First</th>
                <th className="py-1 text-right font-medium">Latest</th>
                <th className="py-1 text-right font-medium">Change</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-100 dark:divide-stone-800">
              {visible.map((product) => (
                <tr
                  key={product.upc}
                  onClick={() => onSelect(product.upc)}
                  className={`cursor-pointer hover:bg-stone-50 dark:hover:bg-stone-800 ${
                    current?.upc === product.upc ? "bg-stone-100 dark:bg-stone-800" : ""
                  }`}
                >
                  <td className="py-1 pr-2">{product.description}</td>
                  <td className="py-1 text-right tabular-nums">{product.observations}</td>
                  <td className="py-1 text-right tabular-nums">
                    {money(product.first_price)}
                  </td>
                  <td className="py-1 text-right tabular-nums">
                    {money(product.last_price)}
                  </td>
                  <td className="py-1 text-right tabular-nums">
                    <Change value={product.change_pct} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {control}
      </Card>
    </div>
  );
}

function Change({ value }: { value: number | null }) {
  if (value === null) return <span>—</span>;
  const tone = value > 2 ? "bad" : value < -2 ? "good" : "neutral";
  return <Pill tone={tone}>{percent(value)}</Pill>;
}

function Series({ series }: { series: PriceSeries }) {
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline gap-2">
        <h3 className="text-sm font-medium">{series.description}</h3>
        <span className="font-mono text-[11px] text-stone-400">{series.upc}</span>
        <span className="ml-auto text-xs text-stone-500 dark:text-stone-400">
          {series.observations} purchases · {series.first_seen} → {series.last_seen} ·
          low {money(series.min_price)} · high {money(series.max_price)}
        </span>
      </div>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={series.points}
            margin={{ top: 4, right: 8, bottom: 4, left: 4 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.12} />
            <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
            <YAxis
              tick={{ fontSize: 11 }}
              width={56}
              domain={["auto", "auto"]}
              tickFormatter={(v) => money(v)}
            />
            <Tooltip
              formatter={(value: number) => money(value)}
              contentStyle={{ fontSize: 12 }}
            />
            <Line
              type="monotone"
              dataKey="retail_amt"
              dot={{ r: 2 }}
              strokeWidth={2}
              stroke="currentColor"
              className="text-stone-600 dark:text-stone-300"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
