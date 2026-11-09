"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { SaturationChart } from "@/components/SaturationChart";
import { api, type RoundResult, type RunEvent, type RunSnapshot } from "@/lib/api";

const STAGES = [
  "scout_sources", "approve_sources", "places_sweep", "extract_lists", "merge_dedupe",
  "measure_yield", "expand_queries", "place_details", "classify", "research_agent",
  "verify_evidence", "contacts", "score", "validate", "export",
];

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const [run, setRun] = useState<RunSnapshot | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [streamKey, setStreamKey] = useState(0); // reconnect SSE after approval

  const refresh = useCallback(() => api.run(id).then(setRun).catch((e) => setError(String(e))), [id]);

  useEffect(() => {
    refresh();
    const es = new EventSource(api.eventsUrl(id));
    const onEvent = (msg: MessageEvent) => {
      const e = JSON.parse(msg.data) as RunEvent;
      setEvents((prev) => [...prev, e]);
      if (e.type === "status" || e.type === "end" || e.type === "node_end") refresh();
    };
    for (const t of ["node_start", "node_end", "status", "company_researched", "end"]) {
      es.addEventListener(t, onEvent as EventListener);
    }
    es.addEventListener("end", () => es.close());
    es.onerror = () => es.close();
    return () => es.close();
  }, [id, refresh, streamKey]);

  if (!run) return <p className="text-muted">{error ?? "Loading…"}</p>;

  const done = new Set([
    ...events.filter((e) => e.type === "node_end").map((e) => e.node),
    ...(run.nodes_done ?? []),
    ...(run.status === "finished" ? ["export"] : []),
  ]);
  const active = run.status === "running" ? run.stage : null;
  const rounds: RoundResult[] = run.rounds?.length
    ? run.rounds
    : events.flatMap((e) => (e.round_result ? [e.round_result] : []));
  const lastCounts = [...events].reverse().find((e) => e.counts && Object.keys(e.counts).length)?.counts ?? run.counts ?? {};
  const researched = events.filter((e) => e.type === "company_researched").length;
  const u = run.usage ?? {};

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="font-mono text-lg">{run.run_id}</h1>
        <StatusBadge status={run.status} />
      </div>
      {run.error && <p className="rounded-md bg-red-500/10 p-2 text-sm text-red-600">{run.error}</p>}

      <div className="grid gap-3 sm:grid-cols-5">
        <Stat label="Places requests" value={u.places_requests ?? 0} />
        <Stat label="LLM cost" value={`$${(u.llm_cost_usd ?? 0).toFixed(3)}`} />
        <Stat label="Unique companies" value={lastCounts.final_unique_companies ?? lastCounts.unique_companies ?? rounds.at(-1)?.cumulative ?? "—"} />
        <Stat label="Property managers" value={lastCounts.property_managers ?? run.counts?.property_managers ?? "—"} />
        <Stat label="Researched" value={researched || run.researched || "—"} />
      </div>

      {run.status === "awaiting_approval" && run.pending_approval && (
        <ApprovalPanel id={id} candidates={run.pending_approval.candidates} onDone={() => {
          setEvents([]); // the server replays the full history on the new connection
          setStreamKey((k) => k + 1);
        }} />
      )}

      <div className="grid gap-5 md:grid-cols-[1fr_1.2fr]">
        <section className="rounded-lg border border-line bg-panel p-4">
          <h2 className="mb-3 font-semibold">Stages</h2>
          <ol className="space-y-1 text-sm">
            {STAGES.map((s) => {
              const state = active === s ? "active" : done.has(s) ? "done" : "todo";
              return (
                <li key={s} className="flex items-center gap-2">
                  <span className={`inline-block h-2.5 w-2.5 rounded-full ${state === "done" ? "bg-accent" : state === "active" ? "animate-pulse bg-amber-500" : "bg-line"}`} />
                  <span className={state === "todo" ? "text-muted" : ""}>{s}</span>
                </li>
              );
            })}
          </ol>
        </section>
        <section className="rounded-lg border border-line bg-panel p-4">
          <h2 className="mb-3 font-semibold">Saturation: cumulative unique companies per round</h2>
          <SaturationChart rounds={rounds} />
        </section>
      </div>

      {run.status === "finished" && (
        <div className="flex gap-3">
          <Link href={`/runs/${id}/results`} className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white">
            View results
          </Link>
          <a href={api.xlsxUrl(id)} className="rounded-md border border-line bg-panel px-4 py-2 text-sm">
            Download xlsx
          </a>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-line bg-panel p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tone: Record<string, string> = {
    running: "bg-amber-500/15 text-amber-700",
    awaiting_approval: "bg-accent/15 text-accent",
    finished: "bg-green-600/15 text-green-700",
    failed: "bg-red-500/15 text-red-600",
  };
  return <span className={`rounded-full px-3 py-1 text-xs font-medium ${tone[status] ?? "bg-line text-muted"}`}>{status.replace("_", " ")}</span>;
}

function ApprovalPanel({ id, candidates, onDone }: { id: string; candidates: { url: string; source_type: string; estimated_count: number | null; notes: string }[]; onDone: () => void }) {
  const [picked, setPicked] = useState<Set<number>>(new Set(candidates.map((_, i) => i)));
  const [extra, setExtra] = useState("");
  const [busy, setBusy] = useState(false);
  const toggle = (i: number) => {
    const next = new Set(picked);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    setPicked(next);
  };
  async function submit() {
    setBusy(true);
    await api.approve(id, [...picked].sort((a, b) => a - b), extra.split(",").map((s) => s.trim()).filter(Boolean));
    onDone();
  }
  return (
    <section className="rounded-lg border-2 border-accent bg-panel p-4">
      <h2 className="font-semibold">Approve list sources</h2>
      <p className="mb-3 text-sm text-muted">The scout agent found these pages. Only approved sources are extracted.</p>
      <ul className="space-y-2">
        {candidates.map((c, i) => (
          <li key={c.url} className="flex gap-3 text-sm">
            <input type="checkbox" checked={picked.has(i)} onChange={() => toggle(i)} className="mt-1" />
            <div>
              <a href={c.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">{c.url}</a>
              <div className="text-xs text-muted">{c.source_type} · ~{c.estimated_count ?? "?"} entries · {c.notes}</div>
            </div>
          </li>
        ))}
      </ul>
      <input className="mt-3 w-full rounded-md border border-line bg-panel px-3 py-2 text-sm" placeholder="Extra list URLs, comma-separated (optional)" value={extra} onChange={(e) => setExtra(e.target.value)} />
      <button onClick={submit} disabled={busy} className="mt-3 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
        {busy ? "Resuming…" : `Approve ${picked.size} and continue`}
      </button>
    </section>
  );
}
