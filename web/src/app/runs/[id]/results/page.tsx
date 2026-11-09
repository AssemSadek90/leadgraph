"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { api, type Lead, type Sourced } from "@/lib/api";

const KPIS: [string, string][] = [
  ["units_managed", "Units"],
  ["management_types", "Types"],
  ["residential_focus", "Residential"],
  ["portfolio_scope", "Scope"],
  ["tenant_portal_or_app", "Portal"],
  ["software_mentioned", "Software"],
  ["metering_provider_mentioned", "Metering"],
  ["association_membership", "Assoc."],
];
const TIER_BG: Record<string, string> = { A: "var(--tier-a)", B: "var(--tier-b)", C: "var(--tier-c)" };

export default function ResultsPage() {
  const { id } = useParams<{ id: string }>();
  const [leads, setLeads] = useState<Lead[]>([]);
  const [tier, setTier] = useState<string>("all");
  const [view, setView] = useState<"leads" | "review">("leads");
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.leads(id).then(setLeads).catch((e) => setError(String(e)));
  }, [id]);

  const rows = useMemo(() => {
    const order = { A: 0, B: 1, C: 2 } as Record<string, number>;
    return leads
      .filter((l) => (view === "review" ? l.needs_review && l.is_property_manager !== false : l.is_property_manager))
      .filter((l) => tier === "all" || l.tier === tier)
      .filter((l) => !q || l.name.toLowerCase().includes(q.toLowerCase()))
      .sort((a, b) => (order[a.tier ?? ""] ?? 3) - (order[b.tier ?? ""] ?? 3) || (b.fit_score ?? 0) - (a.fit_score ?? 0));
  }, [leads, tier, view, q]);

  async function decide(companyId: string, decision: "approve" | "reject" | "clear") {
    const res = await api.review(id, companyId, decision);
    setLeads((prev) => prev.map((l) => (l.id === companyId ? { ...l, review_decision: res.review_decision } : l)));
  }

  const counts = { A: 0, B: 0, C: 0 } as Record<string, number>;
  leads.filter((l) => l.is_property_manager && l.tier).forEach((l) => (counts[l.tier!] += 1));
  const reviewCount = leads.filter((l) => l.needs_review && l.is_property_manager !== false).length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href={`/runs/${id}`} className="text-sm text-accent hover:underline">← run</Link>
          <h1 className="font-mono text-lg">{id}</h1>
        </div>
        <a href={api.xlsxUrl(id)} className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white">Download xlsx</a>
      </div>
      {error && <p className="rounded-md bg-red-500/10 p-2 text-sm text-red-600">{error}</p>}

      <div className="flex flex-wrap items-center gap-2 text-sm">
        {(["leads", "review"] as const).map((v) => (
          <button key={v} onClick={() => setView(v)} className={`rounded-md border px-3 py-1.5 ${view === v ? "border-accent bg-accent text-white" : "border-line bg-panel"}`}>
            {v === "leads" ? "Leads" : `Review queue (${reviewCount})`}
          </button>
        ))}
        <span className="mx-2 h-5 w-px bg-line" />
        {["all", "A", "B", "C"].map((t) => (
          <button key={t} onClick={() => setTier(t)} className={`rounded-md border px-3 py-1.5 ${tier === t ? "border-accent font-medium" : "border-line"} bg-panel`}>
            {t === "all" ? "All tiers" : `Tier ${t} (${counts[t]})`}
          </button>
        ))}
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name" className="ml-auto rounded-md border border-line bg-panel px-3 py-1.5" />
      </div>

      <div className="overflow-x-auto rounded-lg border border-line bg-panel">
        <table className="w-full text-sm">
          <thead className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className="px-3 py-2">Company</th>
              <th className="px-2 py-2">Tier</th>
              <th className="px-2 py-2 text-right">Score</th>
              {KPIS.map(([, l]) => <th key={l} className="px-2 py-2">{l}</th>)}
              <th className="px-3 py-2">Top contact</th>
              <th className="px-3 py-2">{view === "review" ? "Why review · decision" : "Review"}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((l) => (
              <tr key={l.id} className="align-top">
                <td className="px-3 py-2">
                  <div className="font-medium">{l.name}</div>
                  {l.website && <a href={l.website} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline">{l.website.replace(/^https?:\/\//, "").replace(/\/$/, "")}</a>}
                </td>
                <td className="px-2 py-2">
                  {l.tier && <span className="rounded px-2 py-0.5 text-xs font-semibold" style={{ background: TIER_BG[l.tier] }}>{l.tier}</span>}
                </td>
                <td className="px-2 py-2 text-right tabular-nums">{l.fit_score ?? "—"}</td>
                {KPIS.map(([k]) => <KpiCell key={k} item={l.kpis?.[k]} />)}
                <td className="px-3 py-2 text-xs">
                  {l.contacts[0] ? (
                    <>
                      <div className="font-medium">{l.contacts[0].name}</div>
                      <div className="text-muted">{l.contacts[0].role}</div>
                      {l.contacts[0].email && <div>{l.contacts[0].email} <span className="text-muted">({l.contacts[0].email_type})</span></div>}
                      {l.contacts[0].linkedin_url && <a className="text-accent hover:underline" href={l.contacts[0].linkedin_url} target="_blank" rel="noreferrer">LinkedIn (unverified)</a>}
                    </>
                  ) : <span className="text-muted">—</span>}
                </td>
                <td className="px-3 py-2 text-xs">
                  {view === "review" && <div className="mb-1 max-w-56 text-muted">{l.review_reasons.join(", ")}</div>}
                  <div className="flex gap-1">
                    <button onClick={() => decide(l.id, "approve")} className={`rounded border px-2 py-0.5 ${l.review_decision?.decision === "approve" ? "border-green-600 bg-green-600 text-white" : "border-line"}`}>approve</button>
                    <button onClick={() => decide(l.id, "reject")} className={`rounded border px-2 py-0.5 ${l.review_decision?.decision === "reject" ? "border-red-600 bg-red-600 text-white" : "border-line"}`}>reject</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && <p className="p-4 text-sm text-muted">No rows.</p>}
      </div>
      <p className="text-xs text-muted">Hover a KPI value to see the verbatim evidence and its source page.</p>
    </div>
  );
}

function KpiCell({ item }: { item?: Sourced }) {
  const v = item?.value;
  if (v === null || v === undefined || (Array.isArray(v) && !v.length)) {
    return <td className="px-2 py-2 text-muted">—</td>;
  }
  const text = Array.isArray(v) ? v.join(" · ") : typeof v === "boolean" ? (v ? "yes" : "no") : String(v);
  return (
    <td className="group relative px-2 py-2 text-xs">
      <span className="cursor-help border-b border-dotted border-muted">{text}</span>
      <div className="invisible absolute left-0 top-full z-10 mt-1 w-72 rounded-md border border-line bg-panel p-2 text-xs shadow-lg group-hover:visible">
        <div className="italic">“{item?.evidence}”</div>
        {item?.source_url && <a href={item.source_url} target="_blank" rel="noreferrer" className="mt-1 block truncate text-accent hover:underline">{item.source_url}</a>}
        {item?.confidence && <div className="mt-1 text-muted">confidence: {item.confidence}</div>}
      </div>
    </td>
  );
}
