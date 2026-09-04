import { useState } from "react";
import { api } from "../api";
import { useAsync } from "../components/useAsync";
import { Aside, ErrorBox, Spine, Spinner } from "../components/ui";
import { useShowMore } from "../components/ShowMore";
import { categoryVar, money, percent } from "../format";
import type { PricePoint, PriceSeries } from "../types";

/**
 * What each product has cost you over the coverage window. See DESIGN.md.
 *
 * **A line carries an amount and nothing else.** No quantity, no weight. So an
 * amount at twice another can be a price rise or a second item in the trolley,
 * and an amount that moves every trip can be a per-pound product weighed at the
 * till. The response does not say which, and for a real report that ambiguity
 * touches roughly two products in five.
 *
 * So this view no longer draws one line through everything. Products whose
 * amounts behave like a unit price get a series. The rest are listed with what
 * their amounts actually look like, and no price change is claimed for them.
 * Naming the gap is the job; inventing a unit price would be the opposite of it.
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

  const { products, product_count, priceable_count, quantity_disclosed } = history.data;
  const priceable = products.filter((p) => p.priceable);
  const rest = products.filter((p) => !p.priceable);
  const current = products.find((p) => p.upc === selected) ?? priceable[0] ?? products[0];

  return (
    <PriceBody
      priceable={priceable}
      rest={rest}
      productCount={product_count}
      priceableCount={priceable_count}
      quantityDisclosed={quantity_disclosed}
      current={current}
      onSelect={setSelected}
      minObservations={minObservations}
      setMinObservations={setMinObservations}
    />
  );
}

function PriceBody({
  priceable,
  rest,
  productCount,
  priceableCount,
  quantityDisclosed,
  current,
  onSelect,
  minObservations,
  setMinObservations,
}: {
  priceable: PriceSeries[];
  rest: PriceSeries[];
  productCount: number;
  priceableCount: number;
  quantityDisclosed: boolean;
  current: PriceSeries | undefined;
  onSelect: (upc: string) => void;
  minObservations: number;
  setMinObservations: (n: number) => void;
}) {
  const { visible, control } = useShowMore(priceable, 30);
  // Capped on the same terms as the table above it. This list used to render
  // every unpriceable product at once against an endpoint that returns up to
  // 200, so 170 rows in one unbroken block was reachable — and the way you
  // reached it was by doing what the empty state tells you to do, which is to
  // lower the threshold to widen the net.
  const { visible: restVisible, control: restControl } = useShowMore(rest, 30);

  // The threshold control lives outside the empty-state branch on purpose.
  // Returning a bare empty state used to unmount it, so raising the threshold
  // too high left the reader looking at "lower the threshold" with no threshold
  // control on screen, and the only escape was reloading the page.
  const threshold = (
    <label className="flex flex-wrap items-center gap-2 text-muted">
      bought at least
      <input
        type="number"
        min={2}
        max={40}
        value={minObservations}
        onChange={(e) => setMinObservations(Number(e.target.value) || 2)}
        className="num w-14 rounded-[2px] border border-line bg-transparent px-2 py-1 text-ink focus:border-accent focus:outline-2 focus:outline-offset-1 focus:outline-accent"
      />
      times
    </label>
  );

  if (productCount === 0) {
    return (
      <Spine margin={<Aside>no series</Aside>}>
        <h2 className="font-serif text-[17px] font-semibold">
          Nothing bought often enough
        </h2>
        <p className="mt-1 mb-4 max-w-[62ch] text-muted">
          No product was bought at least {minObservations} times, so there is no
          series to plot. Lower the threshold to widen the net.
        </p>
        {threshold}
      </Spine>
    );
  }

  return (
    <div className="space-y-8">
      <Spine margin={<Aside>{productCount} bought often</Aside>}>
        <div className="flex items-baseline gap-5">
          <span className="font-serif text-[42px] leading-none font-semibold text-faint tabular-nums">
            {priceableCount}
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="font-serif text-[17px] font-semibold">
              products the response can actually price
            </h2>
            <p className="mt-0.5 max-w-[62ch] text-muted">
              Out of {productCount} bought often enough to try.{" "}
              {productCount - priceableCount === 0
                ? "Every one of them behaves like a price."
                : productCount - priceableCount === 1
                  ? "One other has amounts that are not a unit price, for the reason below."
                  : `The other ${productCount - priceableCount} have amounts that are not a unit price, for the reasons below.`}
            </p>
          </div>
        </div>
        <div className="mt-4">{threshold}</div>
      </Spine>

      {current && (
        <Spine margin={<Aside>{current.upc}</Aside>}>
          {/* The view promises not to claim a price change for a product whose
              amounts are not a unit price, and then drew one anyway: selecting a
              row from the unpriceable table below rendered a full series,
              complete with a line through amounts the classifier had just said
              were not a price. */}
          {current.priceable ? (
            <Series series={current} />
          ) : (
            <Unpriced series={current} />
          )}
        </Spine>
      )}

      {quantityDisclosed || (
        <Spine margin={<Aside>what a line omits</Aside>}>
          <QuantityNote />
        </Spine>
      )}

      <Spine margin={<Aside>priced series</Aside>}>
        <Table products={visible} current={current} onSelect={onSelect} />
        {control}
      </Spine>

      {rest.length > 0 && (
        <Spine margin={<Aside>no unit price</Aside>}>
          <Unpriceable
            products={restVisible}
            total={rest.length}
            current={current}
            onSelect={onSelect}
          />
          {restControl}
        </Spine>
      )}
    </div>
  );
}

/**
 * What a line leaves out, and why it matters here more than anywhere else.
 *
 * An earlier version of this note claimed that buying three of something
 * arrived as three separate lines. Measured across a real response that
 * happened on 0 of 762 product-days: the format puts the trip on one line and
 * multiplies the amount. The claim came from the synthetic fixture, whose
 * generator picks products with replacement, and it was wrong about the real
 * format in the exact place the reader most needed it to be right.
 */
function QuantityNote() {
  return (
    <p className="max-w-[62ch] text-muted">
      A line in this response carries an amount and nothing else: no quantity, no
      weight. Two of something bought together arrive as one line at twice the
      price, and an item sold by the pound arrives at whatever it weighed that
      day. Neither is a price change, and the response gives no way to tell them
      apart from one. Where the amounts give it away, this view says so and
      leaves the price change unclaimed.
    </p>
  );
}

const PLOT_W = 800;
const PLOT_H = 116;

/** Points at their true position in time, which is the whole point. */
function scale(points: PricePoint[]) {
  const t = points.map((p) => Date.parse(p.date));
  const [t0, t1] = [Math.min(...t), Math.max(...t)];
  const span = t1 - t0 || 1;
  const values = points.flatMap((p) => [p.retail_amt, p.paid_amt]);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const range = hi - lo || 1;
  // A little headroom so the extreme points are not welded to the edges.
  const pad = range * 0.12;
  const yLo = lo - pad;
  const yHi = hi + pad;
  return {
    x: (p: PricePoint) => ((Date.parse(p.date) - t0) / span) * PLOT_W,
    y: (v: number) => PLOT_H - ((v - yLo) / (yHi - yLo)) * PLOT_H,
    lo,
    hi,
  };
}

function path(
  points: PricePoint[],
  pick: (p: PricePoint) => number,
  s: ReturnType<typeof scale>,
) {
  return points
    .map((p, i) => `${i ? "L" : "M"}${s.x(p).toFixed(1)},${s.y(pick(p)).toFixed(1)}`)
    .join(" ");
}

function tip(p: PricePoint): string {
  const parts = [`${p.date} · ${money(p.paid_amt)} paid`];
  if (p.saved_amt > 0) parts.push(`${money(p.saved_amt)} off ${money(p.retail_amt)}`);
  if (p.multiple_of) {
    parts.push(
      `about ${p.multiple_of}x the usual amount — consistent with buying ${p.multiple_of}, not a price rise`,
    );
  }
  return parts.join(" · ");
}

function Series({ series }: { series: PriceSeries }) {
  const pts = series.points;
  const s = scale(pts);
  const savedEver = pts.some((p) => p.saved_amt > 0);
  const hue = categoryVar(series.upc);

  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="font-serif text-[17px] font-semibold" style={{ color: hue }}>
          {series.description}
        </h3>
        <span className="num text-[11.5px] text-faint">{series.upc}</span>
      </div>
      <p className="num mt-1 text-[11.5px] text-faint">
        bought {series.purchases} times · {series.first_seen} → {series.last_seen}
        {series.base_price ? ` · usually ${money(series.base_price)}` : ""}
      </p>

      <div className="mt-3 flex gap-3">
        {/* Price reference at the edge instead of a grid. Two numbers say what
            an axis box and five gridlines were saying. */}
        <div
          className="num relative w-12 shrink-0 text-right text-[11px] text-faint"
          style={{ height: PLOT_H }}
        >
          <span className="absolute right-0" style={{ top: s.y(s.hi) - 6 }}>
            {money(s.hi)}
          </span>
          <span className="absolute right-0" style={{ top: s.y(s.lo) - 6 }}>
            {money(s.lo)}
          </span>
        </div>

        <div className="min-w-0 flex-1">
          <svg
            viewBox={`0 0 ${PLOT_W} ${PLOT_H}`}
            preserveAspectRatio="none"
            className="block w-full border-b border-rule"
            style={{ height: PLOT_H }}
            role="img"
            aria-label={`${series.description}: ${series.purchases} purchases from ${series.first_seen} to ${series.last_seen}`}
          >
            <path
              d={path(pts, (p) => p.retail_amt, s)}
              fill="none"
              stroke="currentColor"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
              className="text-line"
            />
            <path
              d={path(pts, (p) => p.paid_amt, s)}
              fill="none"
              stroke={hue}
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
            {/* Keyed on date AND index. A product bought twice on one day gives
                two points the same date, and React then treats them as one
                element: the second silently replaces the first. The response
                puts a repeat purchase on one line so this is rare, but "rare"
                is not "impossible" and the failure is invisible. */}
            {pts.map((p, i) => (
              <g key={`${p.date}-${i}`}>
                {p.multiple_of ? (
                  // Hollow: the amount is there, but it is probably more than
                  // one item, so it is not a point on a price line.
                  <circle
                    cx={s.x(p)}
                    cy={s.y(p.paid_amt)}
                    r={3}
                    fill="var(--paper-page)"
                    stroke={hue}
                    strokeWidth={1.25}
                    vectorEffect="non-scaling-stroke"
                  />
                ) : (
                  <circle cx={s.x(p)} cy={s.y(p.paid_amt)} r={2.5} fill={hue} />
                )}
                {/* The hit target, invisible and much larger than the dot.
                    preserveAspectRatio="none" squashes the 800-unit viewBox into
                    about 580 CSS pixels, so an r=2 dot renders 2.9px wide and is
                    effectively unhoverable. This is what makes the tooltip
                    reachable; the visible dot stays small on purpose. */}
                <circle cx={s.x(p)} cy={s.y(p.paid_amt)} r={14} fill="transparent">
                  <title>{tip(p)}</title>
                </circle>
              </g>
            ))}
          </svg>
          <div className="num flex justify-between pt-1.5 text-[11px] text-faint">
            <span>{series.first_seen}</span>
            <span>{series.last_seen}</span>
          </div>
        </div>
      </div>

      <p className="mt-2 max-w-[62ch] text-[11.5px] text-faint">
        {savedEver && (
          <>The heavier line is what you paid; the lighter one is the shelf price. </>
        )}
        {series.multiple_count > 0 && (
          <>
            {series.multiple_count} hollow{" "}
            {series.multiple_count === 1 ? "point sits" : "points sit"} at a near-exact
            multiple of the usual amount, which is consistent with buying more than
            one rather than with the price changing.{" "}
          </>
        )}
        Hover a point for the date and what it cost.
      </p>
    </div>
  );
}

/**
 * What is shown instead of a series, when there is no unit price to draw.
 *
 * Not an empty state: the product was bought, the amounts are real, and the
 * dates are real. What cannot be drawn is a line through them, because a line
 * asserts that the movement between two points is a price change. So the
 * amounts are listed and the reason is named.
 */
function Unpriced({ series }: { series: PriceSeries }) {
  const hue = categoryVar(series.upc);
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="font-serif text-[17px] font-semibold" style={{ color: hue }}>
          {series.description}
        </h3>
        <span className="num text-[11.5px] text-faint">{series.upc}</span>
      </div>
      <p className="num mt-1 text-[11.5px] text-faint">
        bought {series.purchases} times · {series.first_seen} → {series.last_seen}
      </p>
      <p className="mt-3 max-w-[62ch] text-muted">
        {series.shape === "weight" ? (
          <>
            The amounts for this product never settle and range too widely to be
            one item at a price: that is what a per-pound product weighed at the
            till looks like. No price series is drawn, because a line between two
            of these points would assert a price change that the response does
            not support.
          </>
        ) : (
          <>
            {series.multiple_count} of these amounts sit at a near-exact multiple
            of the usual one, which is consistent with buying more than one
            rather than with the price changing. A line through them would report
            a fuller trolley as inflation, so none is drawn.
          </>
        )}
      </p>
      <div className="mt-4 border-t border-rule">
        <div className="grid grid-cols-[minmax(0,1fr)_6rem] gap-3 py-1.5 text-[11.5px] tracking-[0.05em] text-muted uppercase">
          <span>Date</span>
          <span className="text-right">Amount</span>
        </div>
        {series.points.map((point, i) => (
          <div
            key={`${point.date}-${i}`}
            className="grid grid-cols-[minmax(0,1fr)_6rem] gap-3 border-t border-rule py-1.5"
          >
            <span className="num text-[12.5px]">{point.date}</span>
            <span className="num text-right text-[12.5px]">
              {money(point.paid_amt)}
              {point.multiple_of ? (
                <span className="ml-1.5 text-faint">×{point.multiple_of}?</span>
              ) : null}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Table({
  products,
  current,
  onSelect,
}: {
  products: PriceSeries[];
  current: PriceSeries | undefined;
  onSelect: (upc: string) => void;
}) {
  const cols = "grid-cols-[minmax(0,1fr)_4.5rem_5rem_5rem_4.5rem]";
  return (
    // Four fixed columns plus their gaps need more than a 327px phone measure,
    // so the table scrolls inside its own box rather than dragging the page
    // sideways. Same treatment Compare and the receipt table already use.
    <div className="scroll-x border-t border-rule">
      <div className="min-w-[26rem]">
      <div
        className={`grid ${cols} gap-3 py-1.5 text-[11.5px] tracking-[0.05em] text-muted uppercase`}
      >
        <span>Product</span>
        <span className="text-right">Purchases</span>
        <span className="text-right">First</span>
        <span className="text-right">Latest</span>
        <span className="text-right">Change</span>
      </div>
      {products.map((p) => (
        <button
          key={p.upc}
          onClick={() => onSelect(p.upc)}
          aria-pressed={current?.upc === p.upc}
          className={`grid w-full ${cols} gap-3 border-t border-rule py-2 text-left hover:bg-sunken ${
            current?.upc === p.upc ? "bg-sunken" : ""
          }`}
        >
          <span className="flex min-w-0 items-baseline gap-2">
            <span
              aria-hidden
              className="mt-px h-2 w-2 shrink-0 rounded-full"
              style={{ background: categoryVar(p.upc) }}
            />
            <span className="truncate" title={p.description}>
              {p.description}
            </span>
          </span>
          <span className="num text-right">{p.purchases}</span>
          <span className="num text-right">{money(p.first_price)}</span>
          <span className="num text-right">{money(p.last_price)}</span>
          {/* No pill, no colour. Groceries getting more expensive is the subject
              matter, not an alarm condition. The sign carries the direction. */}
          <span className="num text-right">
            {p.change_pct === null ? "—" : percent(p.change_pct)}
          </span>
        </button>
      ))}
      </div>
    </div>
  );
}

/**
 * Products the response cannot price, and what their amounts look like instead.
 *
 * Listed rather than hidden. That a fifth of a shopping list cannot be priced
 * from a legally compelled disclosure is a finding about the disclosure, and
 * this view is the only place it shows up.
 */
function Unpriceable({
  products,
  total,
  current,
  onSelect,
}: {
  products: PriceSeries[];
  /** Before the cap, so the heading counts what exists rather than what fits. */
  total: number;
  current: PriceSeries | undefined;
  onSelect: (upc: string) => void;
}) {
  const cols = "grid-cols-[minmax(0,1fr)_4.5rem_6rem_8rem]";
  return (
    <div>
      <h3 className="font-serif text-[17px] font-semibold">
        No unit price in the response
      </h3>
      <p className="mt-0.5 mb-4 max-w-[62ch] text-muted">
        {total} of these were bought often enough, but their amounts do not behave
        like the price of one item. Shown so the gap is visible rather than
        silently dropped, with no price change claimed for any of them.
      </p>
      <div className="scroll-x border-t border-rule">
        <div className="min-w-[24rem]">
        <div
          className={`grid ${cols} gap-3 py-1.5 text-[11.5px] tracking-[0.05em] text-muted uppercase`}
        >
          <span>Product</span>
          <span className="text-right">Purchases</span>
          <span className="text-right">Usually</span>
          <span className="text-right">Why not</span>
        </div>
        {products.map((p) => (
          <button
            key={p.upc}
            onClick={() => onSelect(p.upc)}
            aria-pressed={current?.upc === p.upc}
            className={`grid w-full ${cols} gap-3 border-t border-rule py-2 text-left hover:bg-sunken ${
              current?.upc === p.upc ? "bg-sunken" : ""
            }`}
          >
            <span className="flex min-w-0 items-baseline gap-2">
              <span
                aria-hidden
                className="mt-px h-2 w-2 shrink-0 rounded-full"
                style={{ background: categoryVar(p.upc) }}
              />
              <span className="truncate" title={p.description}>
                {p.description}
              </span>
            </span>
            <span className="num text-right">{p.purchases}</span>
            <span className="num text-right">
              {p.base_price === null ? "—" : money(p.base_price)}
            </span>
            <span className="text-right text-muted">
              {p.shape === "weight" ? "sold by weight" : `${p.multiple_count} multi-buys`}
            </span>
          </button>
        ))}
        </div>
      </div>
    </div>
  );
}
