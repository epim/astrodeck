// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
import {
  isUnreadableRow, listSessionRows, type QueuedSession, type UnreadableListRow,
} from "../../../../api/sessions";
import type { SessionListRow, SessionReportSummary, SessionRow } from "../../../../types";

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
  /** The id of the session this one WAITS BEHIND (#598, backlog ruling D-04),
   *  or null/absent when it waits for nothing. Optional so a card built by a
   *  caller that predates the field still type-checks; absent reads as null. */
  queuedBehind?: string | null;
}

/** The id a row's session waits behind, or null. `SessionRow.queued_behind` is
 *  absent from a server that predates #598 (no wait); the strict string check
 *  keeps a malformed value reading as no wait rather than as a session called
 *  "true". */
export function rowQueuedBehind(row: SessionRow): string | null {
  const q = row.queued_behind;
  return typeof q === "string" && q !== "" ? q : null;
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

/** A session file the store cannot read (#242), as the shelf draws it.
 *
 *  NOT A `SessionCardData`, on purpose. A session card has a sub count, dates,
 *  a thumbnail and six verbs, and every one of them reads the file that is
 *  broken; built as one, it would say "0 SUBS" about a ledger nobody can count
 *  and offer RESUME on it. So it is its own type with its own card
 *  (`UnreadableSessionCard`): the name the file carries, its id, the reason as
 *  the server sent it, DELETE, and RESTORE when a backup sits beside it
 *  (#280). It is not folded with the reports either:
 *  a name read out of a damaged file is not evidence enough to claim a night's
 *  report belongs to it. */
export interface UnreadableCardData {
  key: string;
  id: string;
  /** The name inside the file when it has one, else the stem (the server's). */
  name: string;
  reason: string;
  /** A `<id>.json.bak` sits beside the file (or is all that is left of it):
   *  the card offers RESTORE only then (#280). A strict boolean, so a row from
   *  a server that never sends the key reads as no backup. */
  backup: boolean;
  /** The backup is ALL that is left: the session file is gone (#280). Only the
   *  words differ (the delete removes the backup, the restore replaces
   *  nothing), and the rows `runDelete` and `runRestore` look up say so. */
  orphan: boolean;
}

/** The unreadable rows, in the order the server listed them.
 *
 *  `backup` and `orphan` are read off the row as `UnreadableListRow`, the type
 *  the guard deliberately does not narrow to (see `api/sessions.ts`): the
 *  mapper used to drop them, so no card could know whether it had a backup. */
export function unreadableCards(rows: readonly SessionListRow[]): UnreadableCardData[] {
  return rows.filter(isUnreadableRow).map((r) => {
    const row: UnreadableListRow = r;
    return {
      key: `unreadable:${r.id}`, id: r.id, name: r.name, reason: r.unreadable,
      backup: row.backup === true, orphan: row.orphan === true,
    };
  });
}

/** The session cards. Unreadable rows are skipped here - they are
 *  `unreadableCards`' - so nothing below ever reads a count off one. */
export function buildCards(
  rows: readonly SessionListRow[],
  reports: readonly SessionReportSummary[],
): SessionCardData[] {
  const used = new Set<string>();
  const cards: SessionCardData[] = rows
    .filter((r): r is SessionRow => !isUnreadableRow(r))
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
        queuedBehind: rowQueuedBehind(row),
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

// -------------------------------------------------------------- the queue (#598)
//
// A dormant session can WAIT BEHIND the run that is live, else the session that
// is armed, and be armed by that one completing (backlog ruling D-04,
// owner-approved 2026-09-30). The server decides all of it; these read the
// rows it sent, with the SAME choice of what to wait behind, so a verb is
// offered exactly when the server would not answer 409.

/** What a session waits behind, named for the words on screen. The shared
 *  `QueuedSession` (api/sessions.ts), which is also what a patch answers with. */
export type QueueTarget = QueuedSession;

/** How a session's wait stands, for the chip on its card.
 *
 *  `waiting`: the session it waits behind is still going to run by itself
 *  (live, or dormant and armed), so completing is a thing that can happen.
 *
 *  `stranded`: the marker names a session that has gone (abandoned, deleted),
 *  finished (a completion would already have promoted this one) or stopped by
 *  hand (dormant and disarmed: an operator stop is not a completion). The
 *  server never promotes on any of those, so the card says plainly that this
 *  session is waiting behind nothing and offers to arm it now: the failure
 *  the queue exists to prevent, a night spent expecting it to start. */
export type QueueView =
  | { kind: "waiting"; behind: QueueTarget }
  | { kind: "stranded" };

const hasId = (c: SessionCardData): c is SessionCardData & { id: string } => c.id != null;

/** Will this session start by itself, so something can be queued behind it? */
function goesByItself(c: SessionCardData): boolean {
  return c.status === "active" || (c.status === "dormant" && c.autoResume);
}

/** The session `card` would wait behind: the live run first, else the armed
 *  dormant one (the server's choice), never `card` itself. null when there is
 *  neither, which is the reason ARM AS NEXT gives. */
export function queueTargetFor(
  cards: readonly SessionCardData[], card: SessionCardData,
): QueueTarget | null {
  const others = cards.filter(hasId).filter((c) => c.id !== card.id);
  const pick = others.find((c) => c.status === "active")
    ?? others.find((c) => c.status === "dormant" && c.autoResume);
  return pick ? { id: pick.id, name: pick.name } : null;
}

/** The chip a dormant, unarmed session's wait shows, or null when it waits for
 *  nothing. An ARMED session shows none even if a marker is left on it: it
 *  starts in its own right, and "waits for" would be false. */
export function queueViewOf(
  cards: readonly SessionCardData[], card: SessionCardData,
): QueueView | null {
  const behind = card.queuedBehind ?? null;
  if (behind == null || card.status !== "dormant" || card.autoResume) return null;
  const a = cards.find((c) => c.id === behind);
  return a && goesByItself(a)
    ? { kind: "waiting", behind: { id: behind, name: a.name } }
    : { kind: "stranded" };
}

/** The session waiting behind session `id`, for the "next: ..." line beside
 *  `id`'s own armed state, or null. Only one that is really waiting: a
 *  stranded marker is not a next session. */
export function queuedNextOf(
  cards: readonly SessionCardData[], id: string,
): SessionCardData | null {
  return cards.find((c) => c.queuedBehind === id
    && queueViewOf(cards, c)?.kind === "waiting") ?? null;
}

/** Every session whose wait has nothing to wait behind, for the warning that
 *  says it will not start by itself. */
export function strandedQueue(cards: readonly SessionCardData[]): SessionCardData[] {
  return cards.filter((c) => queueViewOf(cards, c)?.kind === "stranded");
}

// ------------------------------------------------------------------ the read

/** How stale the shelf may be before a fresh mount re-reads it. Long enough
 *  that opening and closing the Gallery twice is one request, short enough that
 *  a night that ended while the app sat on another hub is on the shelf when the
 *  operator goes looking for it. */
export const SESSIONS_INDEX_TTL_MS = 30_000;

export interface SessionsIndex {
  /** null until `GET /api/sessions` has answered once. Not `[]`: an unread
   *  shelf and an empty rig are different claims. Unreadable files included
   *  (#242): `buildCards` and `unreadableCards` split them. */
  rows: SessionListRow[] | null;
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
    const [rowsRes, reportsRes] = await Promise.allSettled([listSessionRows(), listReports()]);
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

const NO_CARDS: SessionCardData[] = [];
let shelfMemo: {
  rows: SessionListRow[]; reports: SessionReportSummary[]; cards: SessionCardData[];
} | null = null;

/** The shelf's cards from the shared snapshot, WITHOUT asking for a read: a
 *  card that needs to know what its neighbours are doing (the queue's "what
 *  would I wait behind", #598) reads the snapshot the grid already loaded.
 *  Memoised on the snapshot's own arrays, so thirty cards share one fold
 *  instead of each running `buildCards`. Empty until the shelf has been read,
 *  which every queue answer reads as "nothing else is running or armed". */
export function useShelfCards(): SessionCardData[] {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  if (snap.rows == null || snap.reports == null) return NO_CARDS;
  if (shelfMemo && shelfMemo.rows === snap.rows && shelfMemo.reports === snap.reports) {
    return shelfMemo.cards;
  }
  const cards = buildCards(snap.rows, snap.reports);
  shelfMemo = { rows: snap.rows, reports: snap.reports, cards };
  return cards;
}

/**
 * The number both the chip and the grid show, derived the one way.
 *
 * `null` means nobody has read the shelf yet (or the role may not), which the
 * chip renders as no count at all - a confident `0` there would say the rig is
 * empty in exactly the case where nothing has been checked.
 */
export function galleryCountFrom(
  rows: readonly SessionListRow[] | null,
  reports: readonly SessionReportSummary[] | null,
): number | null {
  if (rows == null || reports == null) return null;
  // The grid draws an unreadable file's card after the session cards (#242),
  // so the chip counts it: the two numbers are equal by construction or not
  // at all.
  return buildCards(rows, reports).length + unreadableCards(rows).length;
}

/** `galleryCountFrom` over the shared snapshot, memoised so the fold does not
 *  re-run on every unrelated store write the shell re-renders for. */
export function useGalleryCountFrom(idx: SessionsIndex): number | null {
  return useMemo(() => galleryCountFrom(idx.rows, idx.reports), [idx.rows, idx.reports]);
}
