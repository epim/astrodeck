// useSessionCards.ts - what the Gallery grid is made of.
//
// TWO LISTS, ONE SHELF. `GET /api/sessions` is the multi-night ledger and is
// the only thing that can be resumed, regraded or deleted. `GET /api/reports`
// goes back further: a night shot before the ledger existed left a report and
// no session, and dropping those cards would make the rig look like it had
// forgotten work it still holds. So report-only nights get a card too - with
// the verbs that need a session id absent rather than faked.
//
// THE FOLD ITSELF NOW LIVES IN `sessionsIndex.ts`, with the read, because the
// GALLERY chip counts the same cards this grid draws and the two must not be
// two derivations of one number.
//
// THUMBNAILS ARE RESOLVED LAZILY AND SEQUENTIALLY. Each non-live card needs its
// newest accepted frame, which means one ledger read; firing seven of those the
// moment the tab opens is the same stampede `lib/thumbQueue` exists to stop on
// the image side. One at a time, newest session first, capped - the cards
// render immediately with the dashed face and fill in.

import { useEffect, useMemo, useState } from "react";

import { getSession } from "../../../../api/sessions";
import type { Session } from "../../../../types";
import { buildCards, useSessionsIndex } from "./sessionsIndex";
import type { SessionCardData } from "./sessionsIndex";

// The pure half of this module - what a card IS, and how the two payloads
// fold into one shelf - lives in `sessionsIndex.ts`, because the GALLERY
// chip needs the same fold without mounting a grid. Re-exported here so
// every existing importer (`SessionCard.tsx`, `cardActions.ts`, the barrel)
// keeps its one import path.
export { buildCards, nightKeyOf, reportMatchesSession } from "./sessionsIndex";
export type { SessionCardData } from "./sessionsIndex";

/** How many cards get a ledger read for their thumbnail. Beyond this the card
 *  still renders, with the dashed face - a picture is not worth an unbounded
 *  number of requests on a field link. */
export const THUMB_RESOLVE_CAP = 12;

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
  // ONE read, shared with the GALLERY chip (`sessionsIndex.ts`). The two used
  // to fetch independently, which is how a chip and the grid under it end up
  // showing different numbers for the same shelf.
  const idx = useSessionsIndex(true);
  const { rows, reports, error, refresh } = idx;
  const [thumbs, setThumbs] = useState<Record<string, string | null>>({});

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
