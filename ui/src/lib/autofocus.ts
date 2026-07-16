// lib/autofocus.ts — pure helpers for the persisted latest-autofocus-run
// record (F5: R2-FOC-01/DOC-FOC-01). No React, no DOM: npx-tsx testable
// (lib/weather.ts precedent).
//
// The `focus` WS event (types.ts FocusEvent — state/points/best, untouched)
// plus the native engine's additive `fit`/`message` fields (server
// focus/native.py _fit_payload) already flow through the store's `focus`
// slice on every tick, including the terminal done/failed tick — so the
// *raw* event data was never local-component-only. What the bus event does
// NOT carry is which provider ran the sweep or which filter was active (the
// server publishes neither), and it has no first-class timestamp of its own.
// normalizeAutofocusResult snapshots those two from the store's own live
// `status` at the exact moment a run reaches a terminal state, stamps the
// event's `ts`, and produces the canonical record the Focus view's Result
// panel renders — separate from the live `focus` slice (which keeps
// streaming intermediate "running" ticks for the in-progress V-curve chart)
// so a fresh run's early empty tick can never blank out the last completed
// run's evidence before its own terminal tick lands.

export interface AutofocusFit {
  method?: string | null;
  r2?: number | null;
  r2s?: Record<string, number | null>;
  curve?: [number, number][]; // [[position, hfr], …] sampled fitted polyline
  trendlines?: {
    left?: { slope: number; r2: number };
    right?: { slope: number; r2: number };
    intersection?: [number, number] | null;
  } | null;
}

export interface AutofocusPoint {
  position: number;
  hfr: number;
  sigma?: number;
}

export interface AutofocusProvider {
  kind: string;
  label: string;
}

export interface AutofocusResult {
  state: "done" | "failed";
  points: AutofocusPoint[];
  best: { position: number; hfr: number | null } | null;
  fit: AutofocusFit | null;
  message: string | null;
  provider: AutofocusProvider | null;
  filter: string | null;
  ts: number; // epoch ms of the terminal event
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function normalizePoints(raw: unknown): AutofocusPoint[] {
  if (!Array.isArray(raw)) return [];
  const out: AutofocusPoint[] = [];
  for (const p of raw) {
    if (!p || typeof p !== "object") continue;
    const rec = p as Record<string, unknown>;
    const position = num(rec.position);
    const hfr = num(rec.hfr);
    if (position === null || hfr === null) continue;
    const sigma = num(rec.sigma);
    out.push(sigma === null ? { position, hfr } : { position, hfr, sigma });
  }
  return out;
}

function normalizeFit(raw: unknown): AutofocusFit | null {
  if (!raw || typeof raw !== "object") return null;
  const f = raw as Record<string, unknown>;
  const curve = Array.isArray(f.curve)
    ? (f.curve as unknown[]).filter(
        (p): p is [number, number] =>
          Array.isArray(p) && p.length === 2 && typeof p[0] === "number" && typeof p[1] === "number",
      )
    : undefined;
  return {
    method: typeof f.method === "string" ? f.method : null,
    r2: num(f.r2),
    r2s: f.r2s && typeof f.r2s === "object" ? (f.r2s as Record<string, number | null>) : undefined,
    curve,
    trendlines:
      f.trendlines && typeof f.trendlines === "object"
        ? (f.trendlines as AutofocusFit["trendlines"])
        : null,
  };
}

/** Build the canonical persisted record from a raw `focus` bus event's
 *  `data` payload — ONLY for a terminal state (done|failed); returns null
 *  for "running" (and any unrecognized state) so the store's case handler
 *  can call this unconditionally on every `focus` tick without an extra
 *  state check of its own. `ctx.provider`/`ctx.filter` are snapshots the
 *  caller reads from its own live `status` slice (the bus event carries
 *  neither); `ctx.tsMs` is the event's own timestamp (ws.ts's `ev.ts`,
 *  seconds, * 1000), not `Date.now()` — so a slow/backpressured bus frame
 *  still stamps the moment the server actually finished, not when the
 *  client happened to receive it. */
export function normalizeAutofocusResult(
  raw: unknown,
  ctx: { provider: AutofocusProvider | null; filter: string | null; tsMs: number },
): AutofocusResult | null {
  if (!raw || typeof raw !== "object") return null;
  const d = raw as Record<string, unknown>;
  const state = d.state;
  if (state !== "done" && state !== "failed") return null;

  const bestRaw = d.best;
  let best: { position: number; hfr: number | null } | null = null;
  if (bestRaw && typeof bestRaw === "object") {
    const position = num((bestRaw as Record<string, unknown>).position);
    if (position !== null) best = { position, hfr: num((bestRaw as Record<string, unknown>).hfr) };
  }

  return {
    state,
    points: normalizePoints(d.points),
    best,
    fit: normalizeFit(d.fit),
    message: typeof d.message === "string" ? d.message : null,
    provider: ctx.provider,
    filter: ctx.filter,
    ts: ctx.tsMs,
  };
}

/** The currently-selected filter name from `status.filterwheel`
 *  ({position, names}) — index-of-names, defensively bounds-checked. */
export function filterNameFromStatus(
  fw: { position: number; names: string[] } | null | undefined,
): string | null {
  if (!fw || !Array.isArray(fw.names)) return null;
  return fw.names[fw.position] ?? null;
}

/** Compact "how long ago" label for the Result panel's persisted-run line
 *  (mirrors lib/weather.ts's agoLabel phrasing but without the staleness
 *  framing — a persisted AF result never goes "stale," it's just old). */
export function afResultAgeLabel(tsMs: number, nowMs: number): string {
  const ageS = Math.max(0, (nowMs - tsMs) / 1000);
  if (ageS < 60) return "just now";
  const mins = Math.round(ageS / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = ageS / 3600;
  if (hrs < 24) return `${hrs.toFixed(1)}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}
