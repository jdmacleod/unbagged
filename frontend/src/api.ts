import type {
  Compare,
  Compliance,
  BasketDetail,
  FollowUpLetter,
  PriceHistory,
  ProductIndex,
  Profile,
  RequestMeta,
  Timeline,
  UploadResult,
} from "./types";

// Same origin, always. The API is served by the same process that served this
// page, and there is nowhere else for a request to go.
const BASE = "/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function get<T>(path: string, params?: Record<string, string | number | undefined>) {
  const url = new URL(BASE + path, window.location.origin);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
  }
  const response = await fetch(url);
  if (!response.ok) throw new ApiError(await detail(response), response.status);
  return (await response.json()) as T;
}

async function detail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    // FastAPI puts a human-readable sentence here, and the adapters write theirs
    // to be shown verbatim. Passing it straight through beats inventing one.
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    /* fall through to the status text */
  }
  return response.statusText || `Request failed (${response.status})`;
}

export const api = {
  health: () => get<{ status: string; version: string }>("/health"),
  requests: () => get<{ requests: RequestMeta[] }>("/requests"),
  request: (id: number) =>
    get<RequestMeta & { warnings: unknown[]; documents: unknown[] }>(`/requests/${id}`),
  timeline: (
    id: number,
    filters: { store?: string; date_from?: string; date_to?: string; q?: string } = {},
  ) => get<Timeline>(`/requests/${id}/timeline`, filters),
  transaction: (txnId: number) => get<BasketDetail>(`/transactions/${txnId}`),
  profile: (id: number) => get<Profile>(`/requests/${id}/profile`),
  compliance: () => get<Compliance>("/compliance"),
  compare: () => get<Compare>("/compare"),
  priceHistory: (id: number, minObservations = 3) =>
    get<PriceHistory>(`/requests/${id}/price-history`, {
      min_observations: minObservations,
    }),
  productIndex: (
    id: number,
    filters: { q?: string; min_purchases?: number } = {},
  ) => get<ProductIndex>(`/requests/${id}/product-index`, filters),
  followUpLetter: (id: number) => get<FollowUpLetter>(`/requests/${id}/follow-up-letter`),

  async upload(files: File[], declaredRetailer?: string): Promise<UploadResult> {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    if (declaredRetailer) form.append("declared_retailer", declaredRetailer);
    const response = await fetch(`${BASE}/requests`, { method: "POST", body: form });
    if (!response.ok) throw new ApiError(await detail(response), response.status);
    return (await response.json()) as UploadResult;
  },

  async deleteRequest(id: number): Promise<void> {
    const response = await fetch(`${BASE}/requests/${id}`, { method: "DELETE" });
    if (!response.ok) throw new ApiError(await detail(response), response.status);
  },
};
