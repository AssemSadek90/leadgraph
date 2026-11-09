// Typed client for the LeadGraph API (api/main.py). Keys are only ever sent in a request body.
export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Usage = {
  places_requests?: number;
  llm_calls?: number;
  llm_cost_usd?: number;
  search_requests?: number;
};
export type RoundResult = {
  round: number;
  queries: string[];
  new_unique: number;
  cumulative: number;
  cumulative_before: number;
  yield: number | null;
};
export type SourceCandidate = {
  url: string;
  source_type: string;
  estimated_count: number | null;
  notes: string;
};
export type RunSnapshot = {
  run_id: string;
  status: string;
  stage: string | null;
  error: string | null;
  command: string;
  city: string;
  area: string | null;
  provider: string;
  model: string | null;
  pending_approval: { candidates: SourceCandidate[] } | null;
  usage: Usage | null;
  rounds: RoundResult[];
  counts: Record<string, number> | null;
  exports: { xlsx: boolean; report: boolean };
  nodes_done: string[];
  researched: number | null;
};
export type RunEvent = {
  type: string;
  ts?: string;
  node?: string;
  round?: number | null;
  seconds?: number;
  status?: string;
  counts?: Record<string, number>;
  round_result?: RoundResult | null;
  usage?: Usage;
  company_id?: string;
  name?: string;
  error?: string | null;
};
export type Sourced = {
  value: unknown;
  source_url: string | null;
  evidence: string | null;
  confidence: string | null;
};
export type Contact = {
  name: string;
  role: string | null;
  email: string | null;
  email_type: string | null;
  linkedin_url: string | null;
  linkedin_verified: boolean;
  source_url: string;
};
export type Lead = {
  id: string;
  name: string;
  website: string | null;
  address: string | null;
  tier: "A" | "B" | "C" | null;
  fit_score: number | null;
  data_completeness: number | null;
  is_property_manager: boolean | null;
  classification_category: string | null;
  kpis: Record<string, Sourced> | null;
  contacts: Contact[];
  needs_review: boolean;
  review_reasons: string[];
  review_decision: { decision: string; note: string | null } | null;
};
export type ModelsResponse = {
  providers: Record<string, { default: string; models: Record<string, unknown> }>;
};

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body.slice(0, 300)}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  models: () => fetch(`${API}/models`).then((r) => json<ModelsResponse>(r)),
  config: () =>
    fetch(`${API}/config`).then((r) =>
      json<{ cities: Record<string, string[]>; segments: string[] }>(r),
    ),
  runs: () => fetch(`${API}/runs`).then((r) => json<RunSnapshot[]>(r)),
  run: (id: string) => fetch(`${API}/runs/${id}`).then((r) => json<RunSnapshot>(r)),
  leads: (id: string) => fetch(`${API}/runs/${id}/leads`).then((r) => json<Lead[]>(r)),
  createRun: (body: unknown) =>
    fetch(`${API}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<{ run_id: string; status: string }>(r)),
  approve: (id: string, approved: number[], extra: string[]) =>
    fetch(`${API}/runs/${id}/approve-sources`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved, extra }),
    }).then((r) => json<{ status: string }>(r)),
  review: (id: string, companyId: string, decision: "approve" | "reject" | "clear") =>
    fetch(`${API}/runs/${id}/review/${encodeURIComponent(companyId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    }).then((r) => json<{ review_decision: Lead["review_decision"] }>(r)),
  xlsxUrl: (id: string) => `${API}/runs/${id}/export.xlsx`,
  eventsUrl: (id: string) => `${API}/runs/${id}/events`,
};
