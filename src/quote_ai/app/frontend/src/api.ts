// The only place the screens talk to the back end. Every write sends the revision the screen showed; a 409 or 422
// comes back with the reason in `detail`, which the screen shows as it is.

export type Requirement = { value: boolean | null; hard: boolean };
export type Extracted = {
  material: string | null;
  use: string | null;
  powder_free: Requirement;
  latex_free: Requirement;
  sterile: Requirement;
  chemo_tested: Requirement;
  sizes: string[];
  quantity: number;
  unit: string | null;
  target_price: { amount: number; currency: string; per_unit: string | null } | null;
  delivery_days: number | null;
  missing: string[];
  notes: string | null;
};
export type Line = {
  line_no: number;
  size: string;
  quantity: number;
  unit: string;
  sku_id: string | null;
  unit_price: number | null;
  list_price: number | null;
};
export type QuoteEvent = {
  action: string;
  from_state: string | null;
  to_state: string;
  actor: string;
  at: string;
  trace_id: string | null;
};
export type Quote = {
  quote_id: string;
  account: string;
  rep: string;
  state: string;
  version: number;
  revision: number;
  request_text: string;
  extracted: Extracted | null;
  lines: Line[];
  product_id: string | null;
  requirements: { version: number; sizes: string[]; quantity: number | null; missing: string[] };
  checks: Record<string, string>;
  price_method: string | null;
  total: number | null;
  label?: string;
  events?: QuoteEvent[];
  // AI-15: the shortlist; AI-21: the pricing policy's answer for this version
  candidates?: { product_id: string; status: string; on_time: boolean | null; label: string | null }[];
  discount?: number | null;
  approval_needed?: "NONE" | "MANAGER" | null;
  approval?: { approver: string; quote_version: number; terms_hash: string } | null;
};

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data: unknown = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = (data as { detail?: unknown }).detail;
    throw new ApiError(r.status, typeof d === "string" ? d : JSON.stringify(d ?? r.statusText));
  }
  return data as T;
}

export const api = {
  get: <T,>(path: string) => call<T>("GET", path),
  post: <T,>(path: string, body: unknown) => call<T>("POST", path, body),
  // AI-09: a file sent as it is (the request PDF), not as JSON
  postFile: async <T,>(path: string, file: File): Promise<T> => {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": file.type || "application/pdf", "X-Filename": file.name },
      body: file,
    });
    const data: unknown = await r.json().catch(() => ({}));
    if (!r.ok) {
      const d = (data as { detail?: unknown }).detail;
      throw new ApiError(r.status, typeof d === "string" ? d : JSON.stringify(d ?? r.statusText));
    }
    return data as T;
  },
};

// Season 2 (AI-08 to AI-11)
export type EvidenceRow = {
  sku_id: string | null;
  product_id: string | null;
  attribute_name: string;
  attribute_value: string;
  unit: string | null;
  source_type: string;
  source_id: string;
  document_id: string | null;
  document_version: number | null;
  test_method: string | null;
  extraction_method: string;
  verification_status: "verified" | "unverified" | "conflicting" | "superseded";
  reason: string;
  verified_by: string | null;
};
export type SupplierDocument = {
  document_id: string;
  document_type: string;
  version: number;
  effective_date: string;
  is_current: boolean;
  verified_by: string | null;
  verified: string[];
  superseded: string[];
  evidence: EvidenceRow[];
};
export type Answer = {
  question: string;
  text: string;
  sql: string | null;
  columns: string[];
  rows: unknown[][];
  asked_as: string;
};

// Season 3 (AI-14): the win chance at several prices. A simulation on demo data; it never sets the price.
export type WinChance = {
  quote_id: string;
  account: string;
  sku_id: string;
  quantity_boxes: number;
  list_price: number;
  prices: { price: number; win_chance: number; price_vs_list_pct: number; needs_approval: boolean }[];
  consistent: boolean;
  problems: string[];
  label: string;
  note: string;
  model: string;
};

// Season 4 (AI-15): the shortlist. Search finds candidates; the rules decide; every check keeps its state.
export type CheckResult = "verified_match" | "verified_mismatch" | "unknown";
export type RequirementCheck = {
  requirement: string;
  result: CheckResult;
  evidence: string[];
  reason: string;
  claims: string[];
};
export type FulfilmentSummary = {
  status: "full" | "partial" | "backorder" | "alternative_date";
  now: number;
  later: number;
  later_day: number | null;
  ready_day: number | null;
  on_time: boolean;
  label: string;
};
export type Candidate = {
  product_id: string;
  name: string;
  status: "eligible" | "needs_evidence" | "excluded";
  checks: RequirementCheck[];
  failed: string[];
  fulfilment?: FulfilmentSummary;
};
export type ShortlistResult = {
  quote: Quote;
  moved: boolean;
  reason: string;
  shortlist: {
    query: string;
    candidates: string[];
    evidence_snapshot: string;
    inventory_snapshot: string;
    eligible: Candidate[];
    needs_evidence: Candidate[];
    excluded: Candidate[];
  };
};

// Season 4 (AI-16, AI-17): the compare view. Values from data, statuses from the rules; every statement cited.
export type SpecCell = {
  value: string | null;
  unit: string | null;
  status: "verified" | "unverified" | "conflicting" | "missing";
  source_id: string | null;
  document_id: string | null;
  document_version: number | null;
};
export type CompareColumn = {
  product_id: string;
  name: string;
  specs: Record<string, SpecCell>;
  completeness: { verified: number; required: number };
  fit: { hard: string | null; price_vs_target: number | null };
  commercial: { list_price: number | null; free_stock: number | null; lead_time_days: number | null };
  status: string | null;
  offer: string | null;
  unresolved: string[];
  failed?: string[];
};
export type CompareTable = {
  quote_id: string;
  version: number;
  attributes: string[];
  columns: CompareColumn[];
  needs_evidence: CompareColumn[];
  label: string;
};
export type Statement = {
  text: string;
  citation: string;
  layer: "business_truth" | "extracted_claim" | "source_truth" | "no_evidence";
};
export type RetrievedChunk = {
  chunk_id: string;
  doc_id: string;
  doc_version: number;
  section: string;
  truth_layer: string;
  text: string;
};
export type Explanation = {
  question: string;
  answer: string;
  statements: Statement[];
  sources: string[];
  retrieved: RetrievedChunk[];
  variant: "build" | "buy";
  asked_as: string;
  cached: boolean;
  trace_id?: string | null; // AI-22: the feedback buttons rate this answer's trace
  fallback?: string; // AI-26: the comparison model failed; the table and the rules' findings, without prose
};

// Season 5 (AI-18 to AI-21): the agent chat, streamed; memory; pricing and approval.
export type SourceView = {
  tool: string;
  source: string;
  badge: string;
  served?: string;
  sql?: string;
  fallback?: string;
  missing?: string;
};
export type Proposal = {
  quote_id: string;
  from_state: string;
  target: string;
  reason: string;
  version: number;
  revision: number;
  idempotency_key: string;
};
export type MemoryProposal = { account: string; fact_key: string; fact_value: string; question: string };
export type ChatDone = {
  trace_id: string | null;
  stop_reason: string;
  facts: { eligibility?: Record<string, string>; prices?: Record<string, { unit_price: number; total: number }> };
  violations: string[];
  usage: { prompt_tokens: number; completion_tokens: number };
};
export type ChatMessage = { role: "user" | "assistant"; content: string };
export type Resume = {
  quote: Quote;
  messages: ChatMessage[];
  customer_facts: { fact_key: string; fact_value: string; confirmed_by: string }[];
  suggested_requirements: Record<string, boolean | string>;
};
export type PricePreview = {
  quote_id: string;
  version: number;
  revision: number;
  product_id: string | null;
  lines: { size: string; sku_id: string; boxes: number; unit_price: number; total: number; price_book_id: string }[];
  total: number;
  band?: {
    list_price: number;
    unit_price: number;
    discount_pct: number;
    margin_pct: number | null; // AI-24: null for a rep (cost is masked); shown to managers
    approval: "NONE" | "MANAGER" | "REFUSED";
    can_request_approval: boolean;
  };
};
export type Me = { user: string; groups: string[]; is_manager: boolean };

// Season 7 (AI-26): the Ops page, for managers. The same quote_ai_monitoring tables as the operating dashboard.
export type FreshnessRow = {
  kind: "source" | "input";
  name: string;
  as_of: string | null;
  age_hours: number | null;
  limit_hours: number | null;
  ok: boolean;
};
export type SloDay = {
  day: string;
  quotes: number;
  p95_latency_s: number;
  under_target_share: number;
  errors: number;
  stale_refusals: number;
  grounded_rate: number | null;
  eligibility_violations: number | null;
  unknown_share: number | null;
  cost_per_quote_usd: number | null;
  ok: boolean;
};
export type CostSummary = { quotes: number; per_quote_usd: number | null; by_step_usd: Record<string, number> };
export type OpsView = {
  status: "green" | "red";
  label: string;
  freshness: FreshnessRow[];
  breaches: string[];
  slo: SloDay[];
  cost: CostSummary;
  quote_cost?: CostSummary;
};
export type InboxItem = {
  quote_id: string;
  account: string;
  rep: string;
  version: number;
  lines: { size: string; sku_id: string; boxes: number; unit_price: number; list_price: number; margin_pct: number | null }[];
  total: number;
  discount_pct: number;
  requirements_version: number;
  delivery_days: number;
  evidence_snapshot: string | null;
  terms_hash: string;
};

// A POST answered with server-sent events (so not EventSource): each "event: x / data: {...}" block is passed on.
export async function postStream(path: string, body: unknown, onEvent: (event: string, data: any) => void) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok || !r.body) {
    const data: unknown = await r.json().catch(() => ({}));
    const d = (data as { detail?: unknown }).detail;
    throw new ApiError(r.status, typeof d === "string" ? d : r.statusText);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) onEvent(event, JSON.parse(data));
    }
  }
}

// One idempotency key per click (AI-21): a double click or a retry sends the same key and gets the first result.
export const newKey = () =>
  typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;

export async function postWithKey<T>(path: string, body: unknown, key: string): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": key },
    body: JSON.stringify(body),
  });
  const data: unknown = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = (data as { detail?: unknown }).detail;
    throw new ApiError(r.status, typeof d === "string" ? d : JSON.stringify(d ?? r.statusText));
  }
  return data as T;
}
