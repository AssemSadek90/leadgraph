"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type ModelsResponse, type RunSnapshot } from "@/lib/api";

const field = "w-full rounded-md border border-line bg-panel px-3 py-2 text-sm";
const label = "mb-1 block text-xs font-medium uppercase tracking-wide text-muted";

export default function NewRunPage() {
  const router = useRouter();
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [cities, setCities] = useState<Record<string, string[]>>({});
  const [runs, setRuns] = useState<RunSnapshot[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    command: "discover",
    city: "berlin",
    area: "mitte",
    provider: "anthropic",
    model: "",
    llm: "",
    places: "",
    search: "",
    maxPlaces: 400,
    maxLlmCost: 1,
    limit: "",
  });

  useEffect(() => {
    api.models().then(setModels).catch((e) => setError(String(e)));
    api.config().then((c) => setCities(c.cities)).catch(() => {});
    api.runs().then(setRuns).catch(() => {});
  }, []);

  const providerModels = models ? Object.keys(models.providers[form.provider]?.models ?? {}) : [];
  const model = form.model || models?.providers[form.provider]?.default || "";
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm({ ...form, [k]: e.target.value, ...(k === "provider" ? { model: "" } : {}) });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await api.createRun({
        command: form.command,
        city: form.city,
        area: form.area || null,
        provider: form.provider,
        model,
        limit: form.limit ? Number(form.limit) : null,
        keys: { llm: form.llm || null, places: form.places || null, search: form.search || null },
        budget: { max_places_requests: Number(form.maxPlaces), max_llm_cost: Number(form.maxLlmCost) },
      });
      router.push(`/runs/${run_id}`);
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-6 md:grid-cols-[1.1fr_1fr]">
      <form onSubmit={submit} className="space-y-4 rounded-lg border border-line bg-panel p-5">
        <h1 className="text-lg font-semibold">New run</h1>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <span className={label}>Command</span>
            <select className={field} value={form.command} onChange={set("command")}>
              <option value="discover">discover — find &amp; classify</option>
              <option value="run">run — thin slice incl. research</option>
            </select>
          </div>
          <div>
            <span className={label}>City / area</span>
            <div className="flex gap-2">
              <select className={field} value={form.city} onChange={set("city")}>
                {Object.keys(cities).length ? Object.keys(cities).map((c) => <option key={c}>{c}</option>) : <option>berlin</option>}
              </select>
              <select className={field} value={form.area} onChange={set("area")}>
                <option value="">whole city</option>
                {(cities[form.city] ?? ["mitte"]).map((a) => <option key={a}>{a}</option>)}
              </select>
            </div>
          </div>
          <div>
            <span className={label}>Provider</span>
            <select className={field} value={form.provider} onChange={set("provider")}>
              {Object.keys(models?.providers ?? { anthropic: 1, openai: 1 }).map((p) => <option key={p}>{p}</option>)}
            </select>
          </div>
          <div>
            <span className={label}>Model</span>
            <select className={field} value={model} onChange={set("model")}>
              {providerModels.map((m) => <option key={m}>{m}</option>)}
            </select>
          </div>
        </div>

        <fieldset className="space-y-3 rounded-md border border-line p-3">
          <legend className="px-1 text-xs text-muted">API keys — keys stay in memory for this run only</legend>
          <input className={field} type="password" placeholder={`${form.provider} API key`} value={form.llm} onChange={set("llm")} autoComplete="off" />
          <input className={field} type="password" placeholder="Google Places API key" value={form.places} onChange={set("places")} autoComplete="off" />
          <input className={field} type="password" placeholder="Serper API key (scout agent, LinkedIn)" value={form.search} onChange={set("search")} autoComplete="off" />
          <p className="text-xs text-muted">Leave empty to use keys configured on the API server.</p>
        </fieldset>

        <div className="grid grid-cols-3 gap-3">
          <div>
            <span className={label}>Max Places requests</span>
            <input className={field} type="number" min={1} value={form.maxPlaces} onChange={set("maxPlaces")} />
          </div>
          <div>
            <span className={label}>Max LLM cost ($)</span>
            <input className={field} type="number" min={0} step={0.5} value={form.maxLlmCost} onChange={set("maxLlmCost")} />
          </div>
          <div>
            <span className={label}>Limit (optional)</span>
            <input className={field} type="number" min={1} value={form.limit} onChange={set("limit")} placeholder="all" />
          </div>
        </div>

        {error && <p className="rounded-md bg-red-500/10 p-2 text-sm text-red-600">{error}</p>}
        <button disabled={busy} className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
          {busy ? "Starting…" : "Start run"}
        </button>
      </form>

      <section className="rounded-lg border border-line bg-panel p-5">
        <h2 className="mb-3 text-lg font-semibold">Recent runs</h2>
        <ul className="divide-y divide-line text-sm">
          {runs.slice(0, 15).map((r) => (
            <li key={r.run_id} className="flex items-center justify-between py-2">
              <Link href={`/runs/${r.run_id}`} className="font-mono text-accent hover:underline">
                {r.run_id}
              </Link>
              <span className="text-xs text-muted">{r.status}</span>
            </li>
          ))}
          {!runs.length && <li className="py-2 text-muted">No runs yet.</li>}
        </ul>
      </section>
    </div>
  );
}
