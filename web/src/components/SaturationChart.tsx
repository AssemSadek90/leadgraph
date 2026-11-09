"use client";

import { useState } from "react";
import type { RoundResult } from "@/lib/api";

// Cumulative unique companies per discovery round — one series, so no legend; the title names it.
export function SaturationChart({ rounds }: { rounds: RoundResult[] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (!rounds.length) {
    return <p className="text-sm text-muted">The curve appears after the first round.</p>;
  }
  const W = 520, H = 220, pad = { l: 48, r: 36, t: 16, b: 32 };
  const maxY = Math.max(...rounds.map((r) => r.cumulative)) * 1.1 || 1;
  const x = (i: number) => pad.l + (rounds.length === 1 ? (W - pad.l - pad.r) / 2 : (i * (W - pad.l - pad.r)) / (rounds.length - 1));
  const y = (v: number) => H - pad.b - (v / maxY) * (H - pad.t - pad.b);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * maxY));
  const path = rounds.map((r, i) => `${i ? "L" : "M"}${x(i)},${y(r.cumulative)}`).join(" ");
  const h = hover === null ? null : rounds[hover];

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Cumulative unique companies per round">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeWidth={1} />
            <text x={pad.l - 8} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--muted)">{t}</text>
          </g>
        ))}
        {rounds.map((r, i) => (
          <text key={r.round} x={x(i)} y={H - 10} textAnchor="middle" fontSize={11} fill="var(--muted)">
            round {r.round}
          </text>
        ))}
        <path d={path} fill="none" stroke="var(--accent)" strokeWidth={2} />
        {rounds.map((r, i) => (
          <g key={r.round} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <circle cx={x(i)} cy={y(r.cumulative)} r={14} fill="transparent" />
            <circle cx={x(i)} cy={y(r.cumulative)} r={hover === i ? 6 : 4} fill="var(--accent)" stroke="var(--panel)" strokeWidth={2} />
          </g>
        ))}
        <text x={x(rounds.length - 1)} y={y(rounds.at(-1)!.cumulative) - 10} textAnchor="end" fontSize={12} fill="var(--ink)">
          {rounds.at(-1)!.cumulative}
        </text>
      </svg>
      {h && (
        <div className="pointer-events-none absolute right-2 top-2 rounded-md border border-line bg-panel p-2 text-xs shadow">
          <div className="font-medium">Round {h.round}</div>
          <div>{h.cumulative} unique (+{h.new_unique})</div>
          <div>yield {h.yield === null ? "—" : `${(h.yield * 100).toFixed(1)} %`}</div>
          <div className="mt-1 max-w-56 text-muted">{h.queries.join(", ")}</div>
        </div>
      )}
      <table className="sr-only">
        <tbody>
          {rounds.map((r) => (
            <tr key={r.round}><td>round {r.round}</td><td>{r.cumulative}</td><td>{r.yield ?? ""}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
