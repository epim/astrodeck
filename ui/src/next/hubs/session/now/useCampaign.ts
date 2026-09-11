// useCampaign.ts - the campaign ledger's data, in one place (plan section A.2).
//
// THE LOAD-BEARING FACT: `GET /api/flows/{id}/tonight` already returns a
// `budget` array that no screen in this app has ever read
// (`server/astrodeck/flows/tonight.py:449`). Each row is
// `{filter, goal_h, banked_h, tonight_h, has_ledger}`, one row per DISTINCT
// capture step, and `banked_h` is hours of ACCEPTED integration folded over the
// whole report archive. That array IS the ledger card's per-filter rows,
// one-to-one. Nothing here computes a banked figure the server did not send.
//
// `banked_h === null` IS NOT ZERO. "0 of 45 banked" says the rig looked and
// found nothing; "not counted" says nobody has looked. Only the first should
// make an operator re-plan a month, so a null renders as an outlined track and
// a dash, never as a bar at 0%.
//
// TONIGHT'S SHARE IS NOT `tonight_h`. `banked_hours_from_reports` folds
// FINISHED reports, and tonight's frames enter the archive only when the report
// is written - so tonight's contribution has to come from the LIVE ledger:
// accepted frames of this night x that filter's exposure. `tonight_h` is what
// the plan INTENDS to add, which is a different claim and is not banked.
//
// WHICH FLOW? Three sources, first hit wins, and NO HIT MEANS NO CAMPAIGN - the
// card is not rendered and the strip is absent. Inventing one would put a
// made-up denominator on the screen an operator uses to decide whether a month
// of nights is on track.
//
// THE ROUTE IS `view.site_derived`-GATED, and that is correct: an audit of this
// codebase recovered the observatory to 2.9 km from three viewer-legal
// requests. A viewer gets no ledger and no strip.

import { useEffect, useState } from "react";
import { flowsApi } from "../../../../lib/flowsApi";
import { listReports } from "../../../../api/reports";
import { endReasonMeta } from "../../../../lib/reportChart";
import { useCan } from "../../../../lib/caps";
import { useResumeArm, useSeq, useStore } from "../../../../store";
import type { SessionReportSummary } from "../../../../types";
import { useActiveSession, useFlowLibrary } from "./sessionData";
import { acceptedByFilter, exposureByFilter, filterColor, tonightNightKey } from "./filters";

/** The prototype's projection, and the only one there is: the server models no
 *  clear-sky forecast that far out. Every string built from it carries a `~`. */
export const CLEAR_HOURS_PER_NIGHT = 3.5;

/** `flows/tonight.py:914`, verbatim first sentence. */
export const NO_LEDGER_NOTE =
  "No session ledger available, so nothing here claims a banked figure.";

/** The prototype's footer (logic.js `a_campNote`), minus its fabricated finish
 *  date - the rig forecasts no such thing. */
export const LEDGER_NOTE =
  "The ledger is the program counter: each dusk the engine re-reads what is "
  + "banked per filter and shoots the shortfall first. Tonight's share is the "
  + "lighter band.";

export interface BudgetRow {
  filter: string;
  goal_h: number;
  /** null means NO LEDGER, which is not the same claim as zero. */
  banked_h: number | null;
  tonight_h: number;
  has_ledger: boolean;
  /** Live share banked TONIGHT, from the session's own accepted frames. */
  live_tonight_h: number;
  color: string;
}

export interface NightCell {
  key: string;
  label: string;
  /** 0..1 */
  fill: number;
  color: string;
  kind: "past" | "tonight" | "planned";
}

export interface NightWindow {
  dusk_unix: number | null;
  dawn_unix: number | null;
  dark_start_unix: number | null;
  dark_end_unix: number | null;
}

export interface CampaignRead {
  flowId: string;
  name: string;
  budget: BudgetRow[];
  hasLedger: boolean;
  goalH: number;
  /** Banked over the archive PLUS tonight's live share. */
  bankedH: number;
  tonightH: number;
  night: number;
  /** DERIVED - there is no persisted planned-night count on the server, so
   *  every string that carries it carries a tilde too (deviation D2). */
  totalNights: number;
  nightsLeft: number;
  summary: string;
  nights: NightCell[];
  /** `LRGB 12 h` - the header's scope clause. */
  scope: string;
  ledgerNote: string | null;
}

export interface CampaignState {
  campaign: CampaignRead | null;
  /** Tonight's dusk/dawn, when the same fetch could read them. Never guessed. */
  night: NightWindow | null;
  loading: boolean;
  /** The server's own `{ok:false, reason}` - rendered as prose in the card,
   *  never as error chrome. */
  refused: string | null;
  error: string | null;
}

const IDLE: CampaignState = {
  campaign: null, night: null, loading: false, refused: null, error: null,
};

// --------------------------------------------------------------- the fetch
// Shared, like `sessionData`: the ledger card, the vitals band's TO DAWN cell
// and (through T-SES-3) the cross-hub strip all want this one payload.

interface Raw { ok: boolean; reason: string; budget: unknown[]; night: NightWindow | null }

let key = "";
let raw: { data: Raw | null; loading: boolean; error: string | null } =
  { data: null, loading: false, error: null };
let fetchedAt = 0;
/** The `sequence.state` the held answer was fetched under. `tonight` is planned
 *  against what the night has already banked, so idle-at-dusk and running are
 *  two different documents from one route. */
let fetchedState = "";
/** The request in flight, shared. Five components read this module (the ledger
 *  card, the run header, the vitals band, the empty screen's flow id and the
 *  cross-hub strip) and they mount in one tick, so without this every one of
 *  them fired its own `GET /api/flows/{id}/tonight` - an astropy ephemeris pass
 *  per request, five times, for one answer. Same shape as `sessionData.ts`. */
let inFlight: Promise<void> | null = null;
const listeners = new Set<() => void>();
const REFETCH_MS = 10 * 60_000;

/** How many components are reading. The slow re-read runs once for all of them
 *  rather than once each. */
let mounted = 0;
let ticker: ReturnType<typeof setInterval> | null = null;

function publish(next: typeof raw): void {
  raw = next;
  for (const fn of listeners) fn();
}

function num(v: unknown, fallback = 0): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function parseNight(v: unknown): NightWindow | null {
  if (!v || typeof v !== "object") return null;
  const n = v as Record<string, unknown>;
  const pick = (k: string): number | null =>
    typeof n[k] === "number" && Number.isFinite(n[k] as number) ? (n[k] as number) : null;
  return {
    dusk_unix: pick("dusk_unix"),
    dawn_unix: pick("dawn_unix"),
    dark_start_unix: pick("dark_start_unix"),
    dark_end_unix: pick("dark_end_unix"),
  };
}

/**
 * Read tonight's budget for `flowId`, at most once.
 *
 * Every caller gets the SAME promise while one is in flight, and a caller whose
 * answer is still fresh gets no request at all. `force` is the slow ticker's
 * door past the freshness window; a change of flow or of `seqState` opens it by
 * itself.
 */
function loadTonight(flowId: string, seqState: string, force = false): Promise<void> {
  if (key === flowId && inFlight) return inFlight;
  const answered = raw.data != null || raw.error != null;
  const stale = force
    || key !== flowId
    || seqState !== fetchedState
    || !answered
    || Date.now() - fetchedAt >= REFETCH_MS;
  if (!stale) return Promise.resolve();

  key = flowId;
  fetchedState = seqState;
  const k = flowId;
  publish({ ...raw, loading: true, error: null });
  const p = flowsApi.tonight(flowId).then(
    (r) => {
      if (key !== k) return;
      fetchedAt = Date.now();
      const rec: Record<string, unknown> = r ?? {};
      publish({
        data: {
          ok: typeof rec.ok === "boolean" ? rec.ok : true,
          reason: typeof rec.reason === "string" ? rec.reason : "",
          budget: Array.isArray(rec.budget) ? (rec.budget as unknown[]) : [],
          night: parseNight(rec.night),
        },
        loading: false,
        error: null,
      });
    },
    (e: Error) => {
      if (key !== k) return;
      fetchedAt = Date.now();
      publish({ data: null, loading: false, error: e.message });
    },
  ).finally(() => { if (inFlight === p) inFlight = null; });
  inFlight = p;
  return p;
}

/** The slow re-read `REFETCH_MS` was only ever used to SUPPRESS. Nothing re-ran
 *  the fetch, so a campaign opened at dusk still showed dusk's banked figure at
 *  02:00 - on the card an operator reads to decide whether to cut the night
 *  short. One interval for every consumer. */
function startTicker(): void {
  if (ticker) return;
  ticker = setInterval(() => {
    if (key) void loadTonight(key, fetchedState, true);
  }, REFETCH_MS);
}

export function resetCampaignForTests(): void {
  key = "";
  fetchedAt = 0;
  fetchedState = "";
  inFlight = null;
  mounted = 0;
  if (ticker) { clearInterval(ticker); ticker = null; }
  raw = { data: null, loading: false, error: null };
  listeners.clear();
}

// ---------------------------------------------------------------- reports

let reportsCache: SessionReportSummary[] | null = null;
let reportsAt = 0;

function useReports(enabled: boolean): SessionReportSummary[] | null {
  const [rows, setRows] = useState<SessionReportSummary[] | null>(reportsCache);
  useEffect(() => {
    if (!enabled) return;
    if (reportsCache && Date.now() - reportsAt < REFETCH_MS) { setRows(reportsCache); return; }
    let live = true;
    void listReports().then(
      (r) => {
        const rows = Array.isArray(r) ? r : [];
        reportsCache = rows; reportsAt = Date.now(); if (live) setRows(rows);
      },
      () => { /* a failed report list costs the night strip, not the ledger */ },
    );
    return () => { live = false; };
  }, [enabled]);
  return rows;
}

// ------------------------------------------------------------ flow id rule

/** The flow this run came from, or null - and null means THERE IS NO CAMPAIGN. */
export function useCampaignFlowId(): { id: string | null; name: string } {
  const seq = useSeq();
  const { session } = useActiveSession();
  const { cards } = useFlowLibrary();
  const resumeArm = useResumeArm();

  // 1. The session records where the work came from (`origin`/`origin_id` on
  //    the server's Session model). It is "" on a session that predates the
  //    field, and STAYS "" - backfilling it would be an invention.
  const origin = session as unknown as { origin?: string; origin_id?: string } | null;
  if (origin?.origin === "flow" && origin.origin_id) {
    const card = cards.find((c) => c.id === origin.origin_id);
    return { id: origin.origin_id, name: card?.name ?? session?.name ?? seq.plan_name ?? "" };
  }

  // 2. A saved flow whose name is the running plan's name.
  if (seq.plan_name) {
    const card = cards.find((c) => c.name === seq.plan_name);
    if (card) return { id: card.id, name: card.name };
  }

  // 3. What is armed to resume, when nothing is running yet.
  const armed = resumeArm?.armed;
  if (armed && armed.origin === "flow" && armed.origin_id) {
    const card = cards.find((c) => c.id === armed.origin_id);
    return { id: armed.origin_id, name: card?.name ?? armed.name };
  }

  return { id: null, name: "" };
}

// ------------------------------------------------------------------- hook

export function useCampaign(): CampaignState {
  const canRead = useCan("view.site_derived");
  const { id: flowId, name } = useCampaignFlowId();
  const { session, row } = useActiveSession();
  const seq = useSeq();
  const [, bump] = useState(0);

  useEffect(() => {
    const fn = () => bump((n) => n + 1);
    listeners.add(fn);
    mounted += 1;
    startTicker();
    return () => {
      listeners.delete(fn);
      mounted -= 1;
      if (mounted === 0 && ticker) { clearInterval(ticker); ticker = null; }
    };
  }, []);

  useEffect(() => {
    if (!flowId || !canRead) {
      if (key !== "") {
        key = "";
        fetchedState = "";
        inFlight = null;
        publish({ data: null, loading: false, error: null });
      }
      return;
    }
    // `seq.state` is a dependency, not decoration: crossing idle -> running (or
    // running -> complete) changes what the server will say about tonight, and
    // the freshness window inside `loadTonight` is what stops five consumers
    // turning one edge into five requests.
    void loadTonight(flowId, seq.state);
  }, [flowId, canRead, seq.state]);

  const reports = useReports(Boolean(flowId) && canRead);

  if (!flowId || !canRead) return IDLE;
  if (raw.error) return { ...IDLE, error: raw.error };
  if (!raw.data) return { ...IDLE, loading: raw.loading };
  if (!raw.data.ok) {
    return { ...IDLE, refused: raw.data.reason || "This flow has nothing to plan tonight." };
  }

  // ---- the per-filter rows, straight from the server's own budget ----------
  const nightKey = tonightNightKey(session);
  const acceptedTonight = acceptedByFilter(session, nightKey);
  const exposures = exposureByFilter(session?.plan);

  const budget: BudgetRow[] = raw.data.budget.map((r) => {
    const o = (r ?? {}) as Record<string, unknown>;
    const filter = String(o.filter ?? "-");
    const liveH = (acceptedTonight.get(filter) ?? 0) * (exposures.get(filter) ?? 0) / 3600;
    return {
      filter,
      goal_h: num(o.goal_h),
      banked_h: typeof o.banked_h === "number" ? o.banked_h : null,
      tonight_h: num(o.tonight_h),
      has_ledger: o.has_ledger === true,
      live_tonight_h: liveH,
      color: filterColor(filter),
    };
  });

  if (budget.length === 0) return IDLE;   // no per-filter goals: not a campaign

  const hasLedger = budget.some((b) => b.has_ledger);
  const goalH = budget.reduce((a, b) => a + b.goal_h, 0);
  const archiveH = budget.reduce((a, b) => a + (b.banked_h ?? 0), 0);
  const tonightH = budget.reduce((a, b) => a + b.live_tonight_h, 0);
  const bankedH = archiveH + tonightH;
  const nightsLeft = Math.ceil(Math.max(0, goalH - bankedH) / CLEAR_HOURS_PER_NIGHT);
  const nightNo = row?.nights ?? (session?.nights?.length ?? 1);
  const totalNights = nightNo + nightsLeft;

  const summary = `night ${nightNo} of ~${totalNights} · ${bankedH.toFixed(1)} of `
    + `${goalH} h · ~${nightsLeft} clear night${nightsLeft === 1 ? "" : "s"} left`;

  // ---- the night strip ----------------------------------------------------
  const perNightGoalS = totalNights > 0 ? (goalH * 3600) / totalNights : 0;
  const past = (Array.isArray(reports) ? reports : [])
    .filter((r) => name && r.plan_name === name)
    .slice(0, 6)
    .reverse();
  const cells: NightCell[] = past.map((r) => ({
    key: r.id,
    // "held" rather than a cloud glyph: night mode collapses warn and bad
    // toward the same red, so status is never carried by hue or glyph alone
    // (deviation D3).
    label: `${monthDay(r.started_at)} ${(r.integration_s / 3600).toFixed(1)}h`
      + (HELD_REASONS.has(r.end_reason ?? "") ? " held" : ""),
    fill: perNightGoalS > 0 ? Math.min(1, r.integration_s / perNightGoalS) : 0,
    color: endColor(r.end_reason),
    kind: "past",
  }));

  const live = seq.state === "running" || seq.state === "holding" || seq.state === "paused";
  if (live) {
    cells.push({
      key: "tonight",
      label: `tonight ${tonightH.toFixed(1)}h`,
      fill: perNightGoalS > 0 ? Math.min(1, (tonightH * 3600) / perNightGoalS) : 0,
      color: "#9B51E0",
      kind: "tonight",
    });
  }
  const plannedShown = Math.min(3, nightsLeft);
  for (let i = 0; i < plannedShown; i++) {
    cells.push({
      key: `planned-${i}`,
      label: `n${nightNo + i + 1} planned`,
      fill: 0,
      color: "transparent",
      kind: "planned",
    });
  }
  if (nightsLeft > plannedShown) {
    cells.push({
      key: "planned-more",
      label: `+${nightsLeft - plannedShown}`,
      fill: 0,
      color: "transparent",
      kind: "planned",
    });
  }

  const palette = paletteWord(budget.map((b) => b.filter));
  return {
    campaign: {
      flowId,
      name,
      budget,
      hasLedger,
      goalH,
      bankedH,
      tonightH,
      night: nightNo,
      totalNights,
      nightsLeft,
      summary,
      nights: cells,
      scope: `${palette} ${goalH} h`,
      ledgerNote: hasLedger ? null : NO_LEDGER_NOTE,
    },
    night: raw.data.night,
    loading: raw.loading,
    refused: null,
    error: null,
  };
}

const HELD_REASONS = new Set(["incomplete", "quality", "dawn_cutoff"]);

function endColor(reason: string | null): string {
  const tone = endReasonMeta(reason).tone;
  if (tone === "good") return "#3ddc97";
  if (tone === "bad") return "#ff5470";
  return "#ffb454";
}

function monthDay(unix: number): string {
  const d = new Date(unix * 1000);
  const m = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getMonth()];
  return `${m} ${d.getDate()}`;
}

/** The palette word for a set of filters - the prototype's own derivation
 *  (logic.js `combine`), used for the header's scope clause. */
export function paletteWord(filters: readonly string[]): string {
  const set = new Set(filters.map((f) => f.trim().toLowerCase()));
  if (set.has("osc") || set.size === 0) return "OSC";
  const narrow = set.has("ha") || set.has("oiii") || set.has("sii")
    || set.has("o3") || set.has("s2");
  if (narrow) return set.has("sii") || set.has("s2") ? "SHO" : "HOO";
  if (set.has("l") || set.has("lum")) return "LRGB";
  return "RGB";
}

/** The store's own weather slice, read here so the ledger card and the armed
 *  rules agree about whether tonight is overridden. */
export function useWeatherIgnored(): boolean {
  return useStore((s) => s.weather?.ignore_tonight === true);
}
