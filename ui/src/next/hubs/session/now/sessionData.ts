// sessionData.ts - ONE fetch of the running session, shared by every component
// on the Now screen.
//
// WHY A MODULE-LEVEL RESOURCE AND NOT A PROP. The desktop SessionColumn and the
// Now screen mount the SAME components (plan section E.4), so none of them may
// take its data as a prop - each reads the store itself and takes only layout
// props. Six of them need the session ledger (the header's target count, the
// pool chips, the channel counts, the integration bar, the ledger card, the
// FILES button's sub count). Six independent `getSession` calls, re-issued
// every time a frame lands, is six copies of a document that can carry hundreds
// of frame rows - over a field link, on a phone, all night.
//
// So the fetch lives here, keyed by (session id, frames_done). Every hook that
// asks during the same key gets the same promise and the same object.
//
// THE KEY IS THE FRAME COUNT, NOT A TIMER. `frames_done` is the engine's own
// counter and it moves exactly when there is something new to read; a poll
// would either lag a frame or spend requests on nights where nothing changed.

import { useEffect, useState } from "react";
import { getSession, listSessions } from "../../../../api/sessions";
import { useStore } from "../../../../store";
import type { FlowCard } from "../../../../lib/flowsApi";
import type { Session, SessionRow } from "../../../../types";

export interface ActiveSession {
  /** The frozen plan + the frame ledger, or null when there is no session (a
   *  quick run has none) or it has not loaded yet. */
  session: Session | null;
  /** The list row, which is where the NIGHT COUNT lives - `session.nights` is
   *  report ids and `row.nights` is their count. */
  row: SessionRow | null;
  loading: boolean;
  /** A failed read is not an empty ledger, and callers must be able to tell. */
  error: string | null;
}

const EMPTY: ActiveSession = { session: null, row: null, loading: false, error: null };

let key = "";
let snapshot: ActiveSession = EMPTY;
const listeners = new Set<() => void>();

function publish(next: ActiveSession): void {
  snapshot = next;
  for (const fn of listeners) fn();
}

function load(id: string, k: string): void {
  publish({ ...snapshot, loading: true, error: null });
  void Promise.allSettled([getSession(id), listSessions()]).then(([s, rows]) => {
    if (key !== k) return;   // a newer key started while this was in flight
    const session = s.status === "fulfilled" ? s.value : null;
    // `Array.isArray`, not a bare `.find`: a malformed or truncated payload
    // must cost the night-count line, not the whole Now screen.
    const row = rows.status === "fulfilled" && Array.isArray(rows.value)
      ? (rows.value.find((r) => r.id === id) ?? null)
      : null;
    const err = s.status === "rejected" ? String((s.reason as Error)?.message ?? s.reason) : null;
    publish({ session, row, loading: false, error: err });
  });
}

/** Drop the cache. Only for tests and for a hub unmount that wants the next
 *  mount to re-read rather than paint a stale ledger. */
export function resetSessionDataForTests(): void {
  key = "";
  snapshot = EMPTY;
  listeners.clear();
}

export function useActiveSession(): ActiveSession {
  const id = useStore((s) => s.sequence.session?.id ?? null);
  const framesDone = useStore((s) => s.sequence.progress?.frames_done ?? 0);
  const [, bump] = useState(0);

  useEffect(() => {
    const fn = () => bump((n) => n + 1);
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  }, []);

  useEffect(() => {
    if (!id) {
      if (key !== "") { key = ""; publish(EMPTY); }
      return;
    }
    const k = `${id}:${framesDone}`;
    if (k === key) return;
    key = k;
    load(id, k);
  }, [id, framesDone]);

  return id ? snapshot : EMPTY;
}

/** The saved-flow library, loaded once. `libraryLoaded` stays FALSE on an
 *  error on purpose (flowsSlice), so "no flows yet" is never painted over a
 *  library the server has. */
export function useFlowLibrary(): { cards: FlowCard[]; loaded: boolean; error: string | null } {
  const cards = useStore((s) => s.flows.cards);
  const loaded = useStore((s) => s.flows.libraryLoaded);
  const error = useStore((s) => s.flows.libraryError);
  const loadLibrary = useStore((s) => s.flowsLoadLibrary);
  useEffect(() => {
    if (!loaded) void loadLibrary();
  }, [loaded, loadLibrary]);
  return { cards, loaded, error };
}
