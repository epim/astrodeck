// filesData.ts - everything the Files sheet knows that is not a pixel.
//
// Three jobs, all of them store-free and (except the two fetch helpers) pure,
// so the arithmetic the DOWNLOAD button prints can be tested without a browser:
//
//   1. THE FOLD. A row in the sheet is one FILTER, and the two things a user
//      wants from it come from two different places. Counts, grades and
//      integration are the LEDGER's answer (`GET /api/sessions/{id}/files`,
//      Wave S5, or the session ledger folded here when that route is not
//      there). Paths and bytes are the LIBRARY's answer (`GET
//      /api/gallery/frames`) - and they have to be, because the thing the zip
//      actually contains is files on disk, not ledger rows. `foldRows` marries
//      them per filter and never invents the half it does not have: a row with
//      no ledger behind it carries `accepted: null`, which is not `0`.
//
//   2. THE SELECTION. `buildSelection` prefers the FILTER form of
//      `GallerySelection` - it names the same frames in sixty characters at
//      any size - and only falls back to a hand-picked path list when the user
//      has un-ticked something. `lib/gallery.ts`'s `downloadPlan` then polices
//      that list against `PICKED_URL_BUDGET`; nothing here truncates.
//
//   3. THE PER-PHONE MEMORY. What this phone has already downloaded, and how
//      fast bytes actually move over the transport it is on. Both live in
//      `localStorage` under `astrodeck-next-*`, both are wrapped in try/catch,
//      and both are convenience, not truth about the rig (ARCHITECTURE.md #9):
//      a phone that has never downloaded anything renders correctly, and a
//      transport that has never been measured produces NO time claim rather
//      than the prototype's hard-coded 38 MB/s.

import { ApiError } from "../../../../api";
import { getCurrentSessionFiles, getSessionFiles } from "../../../../api/sessions";
import { fmtBytes, type GallerySelection } from "../../../../lib/gallery";
import { fmtDuration } from "../../../../lib/eta";
import type {
  GalleryFrame, Session, SessionFileFrame, SessionFilesIndex, SessionFilterFiles,
} from "../../../../types";

// ============================================================ storage keys

/** Per-phone record of which sessions have been downloaded from here. Read by
 *  the Gallery card's on-phone/on-rig line (plan C.3). */
export const DL_KEY = "astrodeck-next-dl";
/** FITS vs JPEG preference, shared with Settings > PHONE > Downloads. */
export const DLPREF_KEY = "astrodeck-next-dlpref";
/** Measured transfer rate, per transport (plan B.6 / deviation D9). */
export const THROUGHPUT_KEY = "astrodeck-next-throughput";

// ================================================== the S5 index and its fold

// The Wave S5 payload's types now live in `ui/src/types.ts` alongside their
// typed wrappers (`getSessionFiles` / `getCurrentSessionFiles`), which landed
// while this sheet was being written. These aliases keep the local vocabulary -
// a "group" is one filter's row - without a second declaration that could drift
// from the server's.
export type SessionFilesFrame = SessionFileFrame;
export type SessionFilesGroup = SessionFilterFiles;
export type { SessionFilesIndex };

/** The server's bucket for frames whose step has been edited out of the plan.
 *  Distinct from `""`, which is a step that genuinely names no filter. */
export const UNKNOWN_FILTER = "?";

export type SessionFilesResult =
  | { index: SessionFilesIndex; state: "ok" }
  /** The route answered 404: either it is not deployed on this rig yet, or
   *  there is no session for this source. Both mean "fall back", and FastAPI
   *  gives the client no way to tell them apart - so neither is an error. */
  | { index: null; state: "absent" }
  | { index: null; state: "error"; message: string };

/** `GET /api/sessions/{id}/files`, or `/current/files` for `id === "current"`.
 *
 *  The feature probe: a rig without Wave S5 answers 404 and the caller folds
 *  the ledger itself. So does a rig WITH the route and no active session, and
 *  FastAPI gives the client no way to tell those apart - which is fine, because
 *  the answer to both is the same fallback. */
export async function fetchSessionFiles(id: string): Promise<SessionFilesResult> {
  try {
    const index = id === "current"
      ? await getCurrentSessionFiles()
      : await getSessionFiles(id);
    return { index, state: "ok" };
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 405)) {
      return { index: null, state: "absent" };
    }
    return {
      index: null,
      state: "error",
      message: e instanceof Error ? e.message : "could not read the session index",
    };
  }
}

/** The same fold the server does, done from a session ledger.
 *
 *  This is the fallback path for a rig that predates Wave S5, and it needs no
 *  `path` on any frame - the filter comes from the plan step, the verdict from
 *  the override/auto pair. `bytes` is 0 here because a browser cannot stat the
 *  rig's disk; `foldRows` fills that in from the library listing, which is the
 *  only honest source for it anyway. */
export function indexFromSession(session: Session): SessionFilesIndex {
  const steps = new Map<string, { filter: string; exposure_s: number }>();
  for (const t of session.plan.targets) {
    for (const st of t.steps) {
      if (st.id) steps.set(st.id, { filter: st.filter ?? "", exposure_s: st.exposure_s });
    }
  }

  const order: string[] = [];
  const groups = new Map<string, SessionFilesGroup>();
  const bucket = (key: string, exposure: number): SessionFilesGroup => {
    let g = groups.get(key);
    if (!g) {
      order.push(key);
      g = {
        filter: key, count: 0, accepted: 0, exposure_s: exposure,
        bytes: 0, integration_s: 0, frames: [],
      };
      groups.set(key, g);
    } else if (!g.exposure_s && exposure) {
      g.exposure_s = exposure;
    }
    return g;
  };

  // Seeded from the plan so the ORDER is the plan's and a channel that has not
  // been shot yet still appears - the same two reasons the server does it.
  for (const t of session.plan.targets) {
    for (const st of t.steps) bucket(st.filter ?? "", st.exposure_s);
  }

  const frames = [...session.frames].sort((a, b) => a.ts - b.ts);
  for (const f of frames) {
    const step = steps.get(f.step_id);
    const key = step ? step.filter : UNKNOWN_FILTER;
    const g = bucket(key, step?.exposure_s ?? 0);
    const accepted = f.override != null ? f.override === "accept" : f.auto_accepted;
    const exposure = step?.exposure_s ?? num(f.metrics.exposure_s) ?? 0;
    g.count += 1;
    if (accepted) {
      g.accepted += 1;
      g.integration_s += exposure;
    }
    g.frames.push({
      id: f.id,
      ts: f.ts,
      bytes: 0,
      accepted,
      override: f.override,
      hfr: num(f.metrics.hfr),
      stars: num(f.metrics.stars),
      guide_rms: num(f.metrics.guide_rms),
      thumb: f.thumb ? `/api/sessions/${session.id}/frames/${f.id}/thumb` : null,
    });
  }

  const by_filter = order.map((k) => groups.get(k) as SessionFilesGroup);
  return {
    target: targetLabel(session),
    totals: {
      frames: by_filter.reduce((a, g) => a + g.count, 0),
      accepted: by_filter.reduce((a, g) => a + g.accepted, 0),
      bytes: 0,
      integration_s: by_filter.reduce((a, g) => a + g.integration_s, 0),
    },
    by_filter,
  };
}

function num(v: number | null | undefined): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** One target names itself; a multi-target plan has no single answer, so the
 *  session's own name is used rather than picking one and implying the rest
 *  are not there. Mirrors `session_files._target_label`. */
export function targetLabel(session: Session): string {
  const names = session.plan.targets.map((t) => t.name).filter(Boolean);
  if (names.length === 1) return names[0];
  return session.name || session.plan.name || names.join(", ");
}

// ==================================================================== rows

export interface FilesRow {
  /** "" is a step with no filter (mono rig, OSC); "?" is a step the plan no
   *  longer has. Both are real answers and neither is "unknown filter". */
  filter: string;
  /** Frames of this filter. The LEDGER's count when there is one, because that
   *  is what the grades below are counted over. */
  subs: number;
  exposureS: number | null;
  /** Bytes on the rig. The LIBRARY's number when it has one - that is what the
   *  zip will actually weigh - falling back to the ledger's stat. */
  bytes: number;
  integrationS: number;
  /** Library paths for this filter, in listing order. Empty when the library
   *  has nothing for it, which is what makes a picked selection impossible and
   *  is said out loud rather than silently producing an empty zip. */
  paths: string[];
  /** null means NOBODY GRADED THESE, which is not the same claim as zero. */
  accepted: number | null;
  rejected: number | null;
  frames: SessionFilesFrame[];
}

/** Marry the library listing to the ledger index, one row per filter.
 *
 *  Order follows the ledger (which follows the plan) and then any filter the
 *  library has that the ledger does not - a frame on disk from a step that was
 *  edited away still costs bytes, so it is still listed. */
export function foldRows(
  gallery: readonly GalleryFrame[],
  index: SessionFilesIndex | null,
): FilesRow[] {
  const byFilter = new Map<string, { paths: string[]; bytes: number; count: number; exposures: number[] }>();
  const galleryOrder: string[] = [];
  for (const f of gallery) {
    const key = f.filter || "";
    let g = byFilter.get(key);
    if (!g) {
      g = { paths: [], bytes: 0, count: 0, exposures: [] };
      byFilter.set(key, g);
      galleryOrder.push(key);
    }
    g.paths.push(f.path);
    g.bytes += f.bytes || 0;
    g.count += 1;
    if (f.exposure_s != null && Number.isFinite(f.exposure_s)) g.exposures.push(f.exposure_s);
  }

  const rows: FilesRow[] = [];
  const seen = new Set<string>();

  for (const g of index?.by_filter ?? []) {
    seen.add(g.filter);
    const lib = byFilter.get(g.filter);
    rows.push({
      filter: g.filter,
      subs: g.count,
      exposureS: g.exposure_s || medianOf(lib?.exposures ?? []),
      bytes: lib?.bytes || g.bytes,
      integrationS: g.integration_s,
      paths: lib?.paths ?? [],
      accepted: g.accepted,
      rejected: g.count - g.accepted,
      frames: g.frames,
    });
  }

  for (const key of galleryOrder) {
    if (seen.has(key)) continue;
    const lib = byFilter.get(key) as { paths: string[]; bytes: number; count: number; exposures: number[] };
    const exposure = medianOf(lib.exposures);
    rows.push({
      filter: key,
      subs: lib.count,
      exposureS: exposure,
      bytes: lib.bytes,
      integrationS: exposure != null ? exposure * lib.count : 0,
      paths: lib.paths,
      accepted: null,
      rejected: null,
      frames: [],
    });
  }

  return rows;
}

/** Median, not mean: one mis-stamped header must not move the exposure the
 *  row prints. `null` when there is nothing to take a median of. */
export function medianOf(values: readonly number[]): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** The label a filter wears in the sheet. Never an empty string on screen. */
export function filterLabel(filter: string): string {
  if (filter === UNKNOWN_FILTER) return "STEP REMOVED";
  return filter || "NO FILTER";
}

/** The row's second line: what is in it and how it was graded. */
export function rowSubtitle(r: FilesRow): string {
  const parts: string[] = [`${r.subs} sub${r.subs === 1 ? "" : "s"}`];
  if (r.exposureS != null) parts.push(`${trimNumber(r.exposureS)} s`);
  if (r.subs === 0) {
    parts.push("nothing banked yet");
  } else if (r.accepted == null) {
    // No verdict is a fact, not a blank: say so rather than implying they all
    // passed (the proto's fixture said "all passed HFR" unconditionally).
    parts.push("not graded");
  } else if (r.rejected === 0) {
    parts.push("all passed the quality gates");
  } else {
    parts.push(`${r.rejected} rejected`);
  }
  return parts.join(" · ");
}

function trimNumber(v: number): string {
  return Number.isInteger(v) ? String(v) : String(Math.round(v * 10) / 10);
}

export interface RowTotals { subs: number; bytes: number; integrationS: number }

export function totalsOf(rows: readonly FilesRow[], ticked?: ReadonlySet<string>): RowTotals {
  let subs = 0, bytes = 0, integrationS = 0;
  for (const r of rows) {
    if (ticked && !ticked.has(r.filter)) continue;
    subs += r.subs;
    bytes += r.bytes;
    integrationS += r.integrationS;
  }
  return { subs, bytes, integrationS };
}

/** Which rows can be downloaded at all: a row with no frames has nothing to
 *  tick, and a row the library cannot see has no path to send. */
export function tickableRows(rows: readonly FilesRow[]): FilesRow[] {
  return rows.filter((r) => r.subs > 0);
}

export interface SelectionScope { q: string; nightFrom: string; nightTo: string }

/**
 * The selection the DOWNLOAD link will carry.
 *
 * FILTER form whenever every downloadable row is ticked, because that sends the
 * filter itself and stays short at any size. A subset falls back to a picked
 * path list, which `downloadPlan` then refuses past `PICKED_URL_BUDGET` rather
 * than truncating (plan deviation D8: one zip per ticked filter is not an
 * option, because iOS serves one file at a time in the foreground).
 *
 * `forcePicked` exists for the MANUAL source, whose rows are the library's
 * light frames with no search term and no night bound. The filter form of that
 * is `q=""`, which server-side means THE WHOLE LIBRARY - calibration frames
 * included. A selection that quietly widens to twenty times what the rows show
 * is the worst possible way to be short.
 */
export function buildSelection(
  rows: readonly FilesRow[],
  ticked: ReadonlySet<string>,
  scope: SelectionScope,
  forcePicked = false,
): GallerySelection {
  const usable = tickableRows(rows);
  const allTicked = usable.length > 0 && usable.every((r) => ticked.has(r.filter));
  if (allTicked && !forcePicked) {
    return { mode: "filter", q: scope.q, nightFrom: scope.nightFrom, nightTo: scope.nightTo };
  }
  const paths: string[] = [];
  for (const r of usable) {
    if (ticked.has(r.filter)) paths.push(...r.paths);
  }
  return { mode: "picked", paths };
}

/** What the ticked set costs, counted the way the zip will count it: by PATHS
 *  when the selection is a picked list, by subs when it is the whole filter. */
export function selectionCost(
  rows: readonly FilesRow[],
  ticked: ReadonlySet<string>,
): { count: number; bytes: number } {
  let count = 0, bytes = 0;
  for (const r of tickableRows(rows)) {
    if (!ticked.has(r.filter)) continue;
    count += r.paths.length || r.subs;
    bytes += r.bytes;
  }
  return { count, bytes };
}

// ================================================= per-phone downloaded set

export interface DlEntry { at: number; n: number }

function readJson<T>(key: string): T | null {
  try {
    const raw = globalThis.localStorage?.getItem(key);
    if (!raw) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    globalThis.localStorage?.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode, quota, a browser set to block site data - none of which is
       worth breaking a download over. */
  }
}

export function readDownloaded(): Record<string, DlEntry> {
  const raw = readJson<Record<string, DlEntry>>(DL_KEY);
  if (!raw || typeof raw !== "object") return {};
  const out: Record<string, DlEntry> = {};
  for (const [k, v] of Object.entries(raw)) {
    if (v && typeof v === "object" && Number.isFinite(v.n)) {
      out[k] = { at: Number(v.at) || 0, n: Number(v.n) || 0 };
    }
  }
  return out;
}

/** Recorded when a download of that session is STARTED - a plain `<a download>`
 *  navigation reports nothing back, so "started" is the only honest claim this
 *  can make, and the Gallery line says "on this phone", not "downloaded". */
export function noteDownloaded(sessionId: string, n: number, nowMs = Date.now()): void {
  if (!sessionId) return;
  const all = readDownloaded();
  const prev = all[sessionId];
  all[sessionId] = { at: nowMs, n: Math.max(n, prev?.n ?? 0) };
  writeJson(DL_KEY, all);
}

export type DlTone = "good" | "warn" | "dim" | "accent";

/** The Gallery card's third line (plan C.3). */
export function downloadedLine(
  entry: DlEntry | undefined,
  accepted: number,
  live: boolean,
): { text: string; tone: DlTone } {
  if (live) return { text: "recording", tone: "accent" };
  if (!entry) return { text: "on the rig only", tone: "dim" };
  if (entry.n >= accepted) return { text: `${entry.n} subs on this phone`, tone: "good" };
  return { text: `${entry.n} of ${accepted} on this phone`, tone: "warn" };
}

// ============================================================== FITS / JPEG

export type DlPref = "fits" | "jpg";

export function readDlPref(): DlPref {
  try {
    return globalThis.localStorage?.getItem(DLPREF_KEY) === "jpg" ? "jpg" : "fits";
  } catch {
    return "fits";
  }
}

export function writeDlPref(p: DlPref): void {
  try {
    globalThis.localStorage?.setItem(DLPREF_KEY, p);
  } catch { /* see writeJson */ }
}

// ============================================================== throughput

export interface ThroughputSample {
  /** Megabytes (1024-based, like `fmtBytes`) per second. */
  mbps: number;
  atMs: number;
  via: string;
}

/** A sample older than this is discarded: it was measured on a different night,
 *  in a different room, probably on a different network. */
export const THROUGHPUT_MAX_AGE_MS = 24 * 3600 * 1000;
const EMA_ALPHA = 0.3;

/** The stored rate for THIS transport, or null. A different `via` is a
 *  different wire and its number would be a lie about this one. */
export function readThroughput(via: string | null, nowMs = Date.now()): ThroughputSample | null {
  const s = readJson<ThroughputSample>(THROUGHPUT_KEY);
  if (!s || !Number.isFinite(s.mbps) || s.mbps <= 0) return null;
  if (nowMs - (Number(s.atMs) || 0) > THROUGHPUT_MAX_AGE_MS) return null;
  if (via && s.via && s.via !== via) return null;
  return { mbps: s.mbps, atMs: Number(s.atMs) || 0, via: s.via ?? "" };
}

export function noteThroughput(mbps: number, via: string, nowMs = Date.now()): void {
  if (!Number.isFinite(mbps) || mbps <= 0) return;
  const prev = readThroughput(via, nowMs);
  const next = prev ? prev.mbps * (1 - EMA_ALPHA) + mbps * EMA_ALPHA : mbps;
  writeJson(THROUGHPUT_KEY, { mbps: next, atMs: nowMs, via });
}

/**
 * A REAL rate off an image this sheet already loaded.
 *
 * `PerformanceResourceTiming` carries `transferSize` and `duration` for every
 * `<img>` the page fetched, so the stack preview measures the transport at no
 * extra cost - no second request, and no Blob. A `transferSize` of 0 is a cache
 * hit or an opaque cross-origin response and is DISCARDED: dividing zero bytes
 * by a real duration would report the transport as infinitely fast.
 */
export function sampleFromResource(url: string, minBytes = 16 * 1024): number | null {
  try {
    const perf = (globalThis as { performance?: { getEntriesByType?: (t: string) => unknown[] } }).performance;
    const entries = perf?.getEntriesByType?.("resource") as
      { name: string; transferSize?: number; duration?: number }[] | undefined;
    if (!entries?.length) return null;
    for (let i = entries.length - 1; i >= 0; i--) {
      const e = entries[i];
      if (!e.name || !e.name.includes(url)) continue;
      const bytes = e.transferSize ?? 0;
      const ms = e.duration ?? 0;
      if (bytes < minBytes || ms <= 0) return null;
      return bytes / 1024 / 1024 / (ms / 1000);
    }
    return null;
  } catch {
    return null;
  }
}

/** "about 4m 10s" for a transfer, or null when nothing has been measured. */
export function etaWords(bytes: number, mbps: number | null): string | null {
  if (!mbps || mbps <= 0 || !Number.isFinite(bytes) || bytes <= 0) return null;
  const seconds = bytes / 1024 / 1024 / mbps;
  return fmtDuration(Math.max(1, Math.round(seconds)));
}

/**
 * The line under the DOWNLOAD button.
 *
 * Three shapes, and the third one is the point: with no measured rate this
 * states the SIZE and makes no time claim at all. The prototype's "~38 MB/s"
 * was a fixture, and an ETA computed from it would be wrong by an order of
 * magnitude over the relay.
 */
export function transferNote(opts: {
  bytes: number;
  via: "direct" | "relay" | null;
  mbps: number | null;
  target: string;
  date: string;
}): { line: string; extra: string | null } {
  const eta = etaWords(opts.bytes, opts.mbps);
  const lands = `lands in Files > AstroDeck > ${opts.target}${opts.date ? ` ${opts.date}` : ""}`;
  if (opts.via === "direct" && eta) {
    return { line: `direct from the rig · about ${eta} · ${lands}`, extra: null };
  }
  if (opts.via === "relay" && eta) {
    return {
      line: `over the relay · about ${eta} · ${lands}`,
      extra: "On the rig's own Wi-Fi this is faster.",
    };
  }
  if (eta) {
    return { line: `about ${eta} at the last measured rate · ${lands}`, extra: null };
  }
  return {
    line: `${fmtBytes(opts.bytes)} to transfer · ${lands}`,
    extra: "No transfer has been timed on this connection yet, so there is no time estimate.",
  };
}
