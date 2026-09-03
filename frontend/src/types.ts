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
  total_pre_discount: number | null;
  item_count: number;
  items_total: number;
  loyalty_total: number;
  provenance: Provenance;
};

export type LineItem = {
  id: number;
  description_raw: string;
  upc: string | null;
  quantity: number | null;
  retail_amt: number | null;
  loyalty_amt: number | null;
  net_amt: number | null;
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
  total_spend: number | null;
  total_loyalty_discount: number | null;
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
  total_spend: number | null;
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

export type PricePoint = { date: string; retail_amt: number; loyalty_amt: number | null };

export type PriceSeries = {
  upc: string;
  description: string;
  observations: number;
  first_seen: string;
  last_seen: string;
  first_price: number;
  last_price: number;
  min_price: number;
  max_price: number;
  change_pct: number | null;
  points: PricePoint[];
};

export type PriceHistory = {
  min_observations: number;
  product_count: number;
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
