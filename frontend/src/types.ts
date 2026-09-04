// Shapes returned by the API. Kept hand-written rather than generated: the API
// is small, and a type that says what a field means is worth more here than one
// that merely matches.

export type Provenance = {
  source_document_id: number | null;
  page: number | null;
  locator: string | null;
};

export type RequestMeta = {
  id: number;
  retailer_id: string;
  display_name: string;
  report_reference: string | null;
  submitted_at: string | null;
  received_at: string | null;
  statute: string;
  period_start: string | null;
  period_end: string | null;
  adapter_schema_version: number | null;
};

export type Basket = {
  id: number;
  occurred_at: string;
  external_order_id: string | null;
  store_code: string | null;
  division_code: string | null;
  channel: string | null;
  tender_type: string | null;
  /** The retailer's own stated pre-discount total for the basket. */
  total_pre_discount: number | null;
  item_count: number;
  /** Summed `retail_amt`: the shelf amount, before loyalty pricing. */
  shelf_total: number;
  /** Summed `loyalty_amt`, which is the price the line cost — NOT a discount to
   *  subtract. Most lines carry a loyalty price equal to the shelf price. */
  paid_total: number;
  /** shelf_total − paid_total. The derived one. */
  saved_total: number;
  /** shelf_total − total_pre_discount. Non-zero means the summed lines disagree
   *  with the total the retailer states for the basket.
   *
   *  **Not a parse fault.** Checked by hand against a real response: of 20
   *  baskets that did not foot, 2 exceeded the stated total by exactly an
   *  itemised statutory fee the total omits, and 18 fell short by a median 3%
   *  with no line accounting for the difference. The discrepancy is in the
   *  response as supplied. Saying "the parse lost a line" here told the reader
   *  the tool was broken when the retailer's arithmetic was.
   *
   *  Null when the retailer stated no total to check against. */
  stated_pre_discount_delta: number | null;
  provenance: Provenance;
};

export type LineItem = {
  id: number;
  description_raw: string;
  upc: string | null;
  quantity: number | null;
  /** The shelf amount for the line. */
  retail_amt: number | null;
  /** What the line cost under the loyalty programme. A price, not a discount. */
  loyalty_amt: number | null;
  /** loyalty_amt, falling back to retail_amt when none was disclosed. */
  paid_amt: number | null;
  /** retail_amt − paid_amt. Zero on a full-price line. */
  saved_amt: number | null;
  category: string | null;
};

export type BasketDetail = Basket & { items: LineItem[] };

export type Stats = {
  /** False when the retailer disclosed no specific pieces of personal
   *  information. Every count below is then null rather than 0, because "0
   *  visits" is a claim about the retailer that such a response never made. */
  disclosed: boolean;
  basket_count: number | null;
  first_visit: string | null;
  last_visit: string | null;
  /** Summed shelf amounts, before loyalty pricing. Not what was spent. */
  total_shelf: number | null;
  /** The summed loyalty prices: what the retailer disclosed the baskets cost.
   *  Not necessarily what left the account — the tender rows are a separate
   *  disclosure and this figure is not reconciled against them. */
  total_paid: number | null;
  /** total_shelf − total_paid. */
  total_saved: number | null;
  line_count: number | null;
  distinct_products: number | null;
  /** Lines naming no product at zero cost. Reported, never folded into products. */
  zero_value_lines: number | null;
  negative_lines: number | null;
  stores: { store_code: string; visits: number }[];
};

export type Timeline = {
  stats: Stats;
  filtered_count: number;
  baskets: Basket[];
};

export type Identity = {
  id: number;
  id_type: string;
  value: string;
  scope: string | null;
  first_seen: string | null;
  provenance: Provenance;
};

export type Inference = {
  id: number;
  label: string;
  value_raw: string;
  value_num: number | null;
  scale: string | null;
  subject: string | null;
  origin: string;
  /** true / false / null — "we don't know" is a third answer, not a false. */
  derivable_from_txns: boolean | null;
  provenance: Provenance;
};

export type Profile = {
  identities: Identity[];
  identity_count: number;
  inferences_by_origin: Record<string, Inference[]>;
  household_scoped: Inference[];
  household_scoped_count: number;
};

export type DisclosureCell = {
  category: string;
  status: "provided" | "partial" | "absent" | null;
  evidence?: string | null;
  notes?: string | null;
  provenance?: Provenance;
};

export type ComplianceRow = RequestMeta & {
  cells: Record<string, DisclosureCell>;
  absent_count: number;
  follow_ups: { id: number; kind: string; description: string; resolved: number }[];
};

export type Compliance = { categories: string[]; rows: ComplianceRow[] };

export type CompareRow = {
  id: number;
  retailer_id: string;
  display_name: string;
  period_start: string | null;
  period_end: string | null;
  /** See Stats.disclosed. Null metrics below mean "not disclosed", not zero. */
  disclosed: boolean;
  visits: number | null;
  /** What actually left the account. Comparing retailers on pre-discount totals
   *  would rank them by who lists higher prices, not by who cost more. */
  total_paid: number | null;
  total_shelf: number | null;
  total_saved: number | null;
  distinct_products: number | null;
  first_visit: string | null;
  last_visit: string | null;
  identifier_count: number | null;
  inference_count: number | null;
  appended_inference_count: number | null;
  /** Never null: what a retailer failed to address is a real finding about it. */
  absent_disclosures: number;
};

export type Compare = { requests: CompareRow[]; comparable: boolean };

/** One day, not one line. Several lines sharing a date are averaged into a
 *  single observation of that day's price; `lines` keeps the raw count, which
 *  is the only trace of quantity a response without a quantity field leaves. */
export type PricePoint = {
  date: string;
  retail_amt: number;
  paid_amt: number;
  saved_amt: number;
  /** 2 or more when this amount is a near-exact multiple of the product's
   *  ordinary price, which is consistent with buying more than one. Null when
   *  it looks like a single item. A suspicion, never a disclosed fact. */
  multiple_of: number | null;
};

/** How a product's amounts behave, which decides whether they are a price.
 *  - `unit`     one item at a broadly stable price; the only priceable shape
 *  - `multiple` some amounts are exact multiples of the usual one
 *  - `weight`   amounts move every trip, as a per-pound item does */
export type PriceShape = "unit" | "multiple" | "weight";

export type PriceSeries = {
  upc: string;
  description: string;
  /** Times this product was bought. One line is one purchase. */
  purchases: number;
  shape: PriceShape;
  /** The amount this product usually sits at. */
  base_price: number | null;
  multiple_count: number;
  /** True only for `unit`. The others have no unit price to plot. */
  priceable: boolean;
  first_seen: string;
  last_seen: string;
  first_price: number;
  last_price: number;
  min_price: number;
  max_price: number;
  /** Null unless the product is priceable, and computed only from amounts that
   *  look like a single item. */
  change_pct: number | null;
  points: PricePoint[];
};

export type PriceHistory = {
  min_observations: number;
  product_count: number;
  /** How many of those have amounts that behave like a price. */
  priceable_count: number;
  /** False when no line in the response carried a quantity, which is the Kroger
   *  case: a product bought twice on one trip is two lines and nothing
   *  distinguishes that from two trips. */
  quantity_disclosed: boolean;
  products: PriceSeries[];
};

export type ParseWarning = {
  severity: string;
  message: string;
  locator: string | null;
};

export type UploadResult = {
  request_id: number;
  retailer_id: string;
  display_name: string;
  confidence: number;
  confident: boolean;
  summary: Record<string, number>;
  warnings: ParseWarning[];
};

export type FollowUpLetter = {
  letter: string;
  absent_categories: string[];
  partial_categories: string[];
  note: string;
};

/** One product in the index. Size is carried as an ordinal `tier`, never as a
 *  pixel value: the API has no business knowing the type scale, and quantising
 *  to five absolute steps is what makes the encoding honest. See DESIGN.md. */
export type IndexEntry = {
  upc: string;
  description: string;
  purchases: number;
  /** 1 (bought once) to 5 (bought most often). Absolute, not normalised. */
  tier: number;
  first_seen: string;
  last_seen: string;
  /** Bought more than once, then not again for six months before the coverage
   *  window closed. An observation about dates, never a claim about you. */
  stopped: boolean;
};

export type IndexTier = {
  tier: number;
  min_purchases: number;
  count: number;
};

export type ProductIndex = {
  /** False when the retailer disclosed no purchase data at all, which is not
   *  the same as having bought nothing. */
  disclosed: boolean;
  /** Before filtering, because the headline is a fact about the response. */
  total_products: number;
  bought_once_total: number;
  /** After filtering. */
  product_count: number;
  bought_once: number;
  min_purchases: number;
  coverage_end: string | null;
  /** Last-bought dates before this count as stopped. */
  stale_before: string | null;
  stopped_count: number;
  tiers: IndexTier[];
  /** True when `products` was cut to `limit`. Said on screen, never silent. */
  truncated: boolean;
  limit: number;
  products: IndexEntry[];
};

/** One adapter the build knows about, and the reading it currently produces.
 *  `schema_version` is compared against a request's `adapter_schema_version`
 *  to tell whether a stored report predates a correction. */
export type AdapterInfo = {
  retailer_id: string;
  display_name: string;
  schema_version: number;
};
