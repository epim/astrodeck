// sessionsIndex.ts - ONE read of what the rig still holds, shared by the
// GALLERY chip and the GALLERY grid.
//
// WHY A MODULE-LEVEL CACHE AND NOT A HOOK PER CALLER. The chip lives in the
// shell (it is on screen on every hub, all night) and the grid lives in the
// Gallery screen. Two independent hooks would mean two `GET /api/sessions` +
// `GET /api/reports` pairs and, worse, two ANSWERS: the chip would keep the
// number it read at launch while the grid drew a newer one, and a chip that
// says 7 over a grid of 8 makes the operator count tiles to work out which
// reading is stale. Here there is one snapshot and both read it, so the two
// numbers are equal BY CONSTRUCTION rather than by both calling the same
// function and hoping they were called at the same time.
//
// AND THE COUNT IS `buildCards(...).length`, NOT `rows.length`. The shelf draws
// a card for a report-only night - one shot before the session ledger existed -
// and folds a report into the session it belongs to. Counting `rows` would
// undercount exactly the nights the grid goes out of its way to keep.
//
// FRESHNESS. The pair is fetched once and then re-read only when a caller asks
// (`refresh()`, which the grid wires to DELETE / ABANDON) or when a mount finds
// the snapshot older than `SESSIONS_INDEX_TTL_MS`. That is the difference
// between the chip being read at launch and still being right when the Gallery
// is opened four hours later, without putting a poll on a field link all night
// for a two-character number.

import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";

import { listReports } from "../../../../api/reports";
import { listSessions } from "../../../../api/sessions";
import type { SessionReportSummary, SessionRow } from "../../../../types";

// ------------------------------------------------------------- what a card is
//
// DE-DUPLICATION IS BY PLAN NAME AND NIGHT, because that is all the two
// payloads share: `SessionReportSummary` carries no session id, and
// `SessionRow` carries no report id. The window is deliberately generous at
// the end (a report is written when the run stops, which can be hours after
// the session row last moved) and tight at the start.

export interface SessionCardData {
  /** Stable React key. A report-only card has no session id, so it is keyed by
   *  the report. */
  key: string;
  /** null for a report-only night: every verb that needs one is absent. */
  id: string | null;
  name: string;
  status: SessionRow["status"] | "report";
  createdTs: number;
  updatedTs: number;
  accepted: number;
  total: number;
  nights: number;
  autoResume: boolean;
  /** null when nothing knows it. NOT zero - "0 min" and "nobody measured" are
   *  different claims. */
  integrationS: number | null;
  reportId: string | null;
}

/** A night key by the same noon rollover the server uses, so a report that
 *  started at 01:00 dedupes against the night it belongs to. */
export function nightKeyOf(unixSeconds: number): string {
  const d = new Date((unixSeconds - 12 * 3600) * 1000);
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/** Does this report belong to that session? Name plus a window: the report is
 *  written at the END of a run, so it can land long after `updated_ts`. */
export function reportMatchesSession(row: SessionRow, r: SessionReportSummary): boolean {
  if (r.plan_name !== row.name) return false;
  return r.started_at >= row.created_ts - 3600 && r.started_at <= row.updated_ts + 12 * 3600;
}

export function buildCards(
  rows: readonly SessionRow[],
  reports: readonly SessionReportSummary[],
): SessionCardData[] {
  const used = new Set<string>();
  const cards: SessionCardData[] = [...rows]
    .sort((a, b) => b.updated_ts - a.updated_ts)
    .map((row) => {
      const hit = reports.find((r) => reportMatchesSession(row, r));
      if (hit) used.add(hit.id);
      return {
        key: row.id,
        id: row.id,
        name: row.name,
        status: row.status,
        createdTs: row.created_ts,
        updatedTs: row.updated_ts,
        accepted: row.accepted,
        total: row.total,
        nights: row.nights,
        autoResume: row.auto_resume,
        integrationS: hit ? hit.integration_s : null,
        reportId: hit?.id ?? null,
      };
    });

  const seen = new Set(cards.map((c) => `${c.name}|${nightKeyOf(c.updatedTs)}`));
  for (const r of reports) {
    if (used.has(r.id)) continue;
    const key = `${r.plan_name}|${nightKeyOf(r.started_at)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    cards.push({
      key: `report:${r.id}`,
      id: null,
      name: r.plan_name,
      status: "report",
      createdTs: r.started_at,
      updatedTs: r.ended_at ?? r.started_at,
      accepted: Math.max(0, r.frames_captured - r.frames_rejected),
      total: r.frames_captured,
      nights: 1,
      autoResume: false,
      integrationS: r.integration_s,
      reportId: r.id,
    });
  }

  return cards.sort((a, b) => b.updatedTs - a.updatedTs);
}

// ------------------------------------------------------------------ the read

/** How stale the shelf may be before a fresh mount re-reads it. Long enough
 *  that opening and closing the Gallery twice is one request, short enough that
 *  a night that ended while the app sat on another hub is on the shelf when the
 *  operator goes looking for it. */
export const SESSIONS_INDEX_TTL_MS = 30_000;

export interface SessionsIndex {
  /** null until `GET /api/sessions` has answered once. Not `[]`: an unread
   *  shelf and an empty rig are different claims. */
  rows: SessionRow[] | null;
  /** null until `GET /api/reports` has answered once. */
  reports: SessionReportSummary[] | null;
  /** Only the session read produces one. A missing report index is not an error
   *  for this screen - the sessions are the shelf; the reports only add older
   *  nights and the integration line. */
  error: string | null;
  loading: boolean;
}

const EMPTY: SessionsIndex = { rows: null, reports: null, error: null, loading: false };

let snapshot: SessionsIndex = EMPTY;
let loadedAtMs: number | null = null;
let inflight: Promise<void> | null = null;
const listeners = new Set<() => void>();

function publish(next: SessionsIndex): void {
  snapshot = next;
  for (const l of [...listeners]) l();
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => { listeners.delete(l); };
}

function getSnapshot(): SessionsIndex {
  return snapshot;
}

/** Read both lists, at most one read in flight at a time.
 *
 *  `force` skips the TTL; without it a call inside the window is a no-op, which
 *  is what makes it safe for every mount to ask. */
export function loadSessionsIndex(force = false): Promise<void> {
  if (inflight) return inflight;
  const fresh = loadedAtMs != null && Date.now() - loadedAtMs < SESSIONS_INDEX_TTL_MS;
  if (fresh && !force) return Promise.resolve();

  publish({ ...snapshot, loading: true });
  const run = (async () => {
    // In parallel: the two reads are independent, and doing them in sequence
    // would put a second round trip in front of the first card on a link where
    // the round trip is the cost.
    const [rowsRes, reportsRes] = await Promise.allSettled([listSessions(), listReports()]);
    const rows = rowsRes.status === "fulfilled" ? rowsRes.value : [];
    const error = rowsRes.status === "fulfilled"
      ? null
      : (rowsRes.reason instanceof Error ? rowsRes.reason.message : "could not read the session list");
    const reports = reportsRes.status === "fulfilled" ? reportsRes.value : [];
    loadedAtMs = Date.now();
    publish({ rows, reports, error, loading: false });
  })().finally(() => { inflight = null; });
  inflight = run;
  return run;
}

/** Drop everything read so far. For tests only - the app has no reason to
 *  forget the shelf, and a caller that wants it re-read wants `refresh()`. */
export function resetSessionsIndex(): void {
  loadedAtMs = null;
  inflight = null;
  publish(EMPTY);
}

/**
 * The shared shelf. `enabled` is the caller's capability answer: with
 * `view.status` withheld the pair is never fetched, so a viewer who cannot read
 * the ledger does not spend the night collecting 403s for a chip.
 */
export function useSessionsIndex(enabled: boolean): SessionsIndex & { refresh: () => void } {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    if (!enabled) return;
    void loadSessionsIndex();
  }, [enabled]);

  const refresh = useCallback(() => { void loadSessionsIndex(true); }, []);

  return { ...snap, refresh };
}

/**
 * The number both the chip and the grid show, derived the one way.
 *
 * `null` means nobody has read the shelf yet (or the role may not), which the
 * chip renders as no count at all - a confident `0` there would say the rig is
 * empty in exactly the case where nothing has been checked.
 */
export function galleryCountFrom(
  rows: readonly SessionRow[] | null,
  reports: readonly SessionReportSummary[] | null,
): number | null {
  if (rows == null || reports == null) return null;
  return buildCards(rows, reports).length;
}

/** `galleryCountFrom` over the shared snapshot, memoised so the fold does not
 *  re-run on every unrelated store write the shell re-renders for. */
export function useGalleryCountFrom(idx: SessionsIndex): number | null {
  return useMemo(() => galleryCountFrom(idx.rows, idx.reports), [idx.rows, idx.reports]);
}
