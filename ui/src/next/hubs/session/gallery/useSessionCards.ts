// useSessionCards.ts - what the Gallery grid is made of.
//
// TWO LISTS, ONE SHELF. `GET /api/sessions` is the multi-night ledger and is
// the only thing that can be resumed, regraded or deleted. `GET /api/reports`
// goes back further: a night shot before the ledger existed left a report and
// no session, and dropping those cards would make the rig look like it had
// forgotten work it still holds. So report-only nights get a card too - with
// the verbs that need a session id absent rather than faked.
//
// DE-DUPLICATION IS BY PLAN NAME AND NIGHT, because that is all the two
// payloads share: `SessionReportSummary` carries no session id, and
// `SessionRow` carries no report id. The window is deliberately generous at the
// end (a report is written when the run stops, which can be hours after the
// session row last moved) and tight at the start.
//
// THUMBNAILS ARE RESOLVED LAZILY AND SEQUENTIALLY. Each non-live card needs its
// newest accepted frame, which means one ledger read; firing seven of those the
// moment the tab opens is the same stampede `lib/thumbQueue` exists to stop on
// the image side. One at a time, newest session first, capped - the cards
// render immediately with the dashed face and fill in.

import { useCallback, useEffect, useMemo, useState } from "react";

import { listReports } from "../../../../api/reports";
import { getSession, listSessions } from "../../../../api/sessions";
import type { Session, SessionReportSummary, SessionRow } from "../../../../types";

/** How many cards get a ledger read for their thumbnail. Beyond this the card
 *  still renders, with the dashed face - a picture is not worth an unbounded
 *  number of requests on a field link. */
export const THUMB_RESOLVE_CAP = 12;

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

/** The newest ACCEPTED frame that actually has a thumbnail, or null. Accepted,
 *  because the card is the night's advertisement and a rejected frame is the
 *  one the engine already judged unfit to show. */
export function newestThumbFrameId(session: Session): string | null {
  const ok = session.frames
    .filter((f) => (f.override != null ? f.override === "accept" : f.auto_accepted) && f.thumb)
    .sort((a, b) => b.ts - a.ts);
  return ok[0]?.id ?? null;
}

export interface SessionCards {
  cards: SessionCardData[];
  loading: boolean;
  error: string | null;
  /** sessionId -> thumbnail path (app-absolute, prefix with `u()`), or null
   *  once it is known there is none. Absent means "not looked yet". */
  thumbs: Record<string, string | null>;
  refresh: () => void;
}

export function useSessionCards(): SessionCards {
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  const [reports, setReports] = useState<SessionReportSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [thumbs, setThumbs] = useState<Record<string, string | null>>({});
  const [gen, setGen] = useState(0);
  const refresh = useCallback(() => setGen((g) => g + 1), []);

  useEffect(() => {
    let alive = true;
    setError(null);
    listSessions()
      .then((r) => { if (alive) setRows(r); })
      .catch((e) => {
        if (!alive) return;
        setRows([]);
        setError(e instanceof Error ? e.message : "could not read the session list");
      });
    // A missing report index is not an error for this screen: the sessions are
    // the shelf, the reports only add older nights and the integration line.
    listReports()
      .then((r) => { if (alive) setReports(r); })
      .catch(() => { if (alive) setReports([]); });
    return () => { alive = false; };
  }, [gen]);

  const cards = useMemo(
    () => (rows && reports ? buildCards(rows, reports) : []),
    [rows, reports],
  );

  useEffect(() => {
    if (!cards.length) return;
    let alive = true;
    (async () => {
      let done = 0;
      for (const c of cards) {
        if (!alive || done >= THUMB_RESOLVE_CAP) return;
        if (!c.id || c.status === "active") continue;
        done++;
        try {
          const s = await getSession(c.id);
          if (!alive) return;
          const fid = newestThumbFrameId(s);
          setThumbs((cur) => ({
            ...cur,
            [c.id as string]: fid ? `/api/sessions/${c.id}/frames/${fid}/thumb` : null,
          }));
        } catch {
          if (!alive) return;
          setThumbs((cur) => ({ ...cur, [c.id as string]: null }));
        }
      }
    })();
    return () => { alive = false; };
  }, [cards]);

  return { cards, loading: rows == null || reports == null, error, thumbs, refresh };
}
