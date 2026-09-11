// sendControl.ts - a run control that still works when the socket is dead.
//
// TRANSCRIBED from `views/MonitorView.tsx:461-482` (UX-41), because the reason
// it exists has not changed: the store's feedback path for a control write is
// log -> toast, and that path RIDES THE WEBSOCKET. So a failed or timed-out
// POST on a down link produced nothing at all on screen - on Abort, which is
// the one control that exists for exactly that situation.
//
// Two further things this must get right, both paid for once already:
//
//   * "Pause sent" was true about the REQUEST and false about the rig. The
//     route returns the instant the flag is set while the exposure it
//     interrupts keeps running, so the caller supplies the success copy.
//   * A TIMED-OUT abort is not a failed abort. `/api/sequence/abort` awaits the
//     entire ~210 s wind-down against a client budget of seconds, so the abort
//     that WORKED comes back rejected. `timedOut` is reported separately and
//     the caller decides; it must never be toasted as a failure.

import { u } from "../../../../lib/base";
import { useStore } from "../../../../store";

export interface ControlOutcome {
  ok: boolean;
  /** The request ran out of time. Says NOTHING about whether the rig acted -
   *  the engine's next state frame is the answer. */
  timedOut: boolean;
  status: number | null;
}

const TIMEOUT_MS = 4000;

function timeoutSignal(ms: number): AbortSignal | undefined {
  if (typeof AbortSignal === "undefined" || !("timeout" in AbortSignal)) return undefined;
  return (AbortSignal as unknown as { timeout(n: number): AbortSignal }).timeout(ms);
}

/** POST `path` over a plain fetch, toast both outcomes, and report which.
 *
 *  `ok` overrides the success copy - "Pause sent" is a sentence about a
 *  request, and the caller is the only one that knows what the rig will
 *  actually do next. */
export async function sendControl(
  label: string,
  path: string,
  ok?: { title: string; detail?: string },
): Promise<ControlOutcome> {
  const toast = useStore.getState().enqueueToast;
  try {
    const res = await fetch(u(path), { method: "POST", signal: timeoutSignal(TIMEOUT_MS) });
    if (!res.ok) {
      toast({ level: "error", title: `${label} failed (${res.status})` });
      return { ok: false, timedOut: false, status: res.status };
    }
    toast({ level: "success", title: ok?.title ?? `${label} sent`, detail: ok?.detail });
    return { ok: true, timedOut: false, status: res.status };
  } catch (e) {
    // A timeout on a route that legitimately outlives the budget is not news
    // the operator can act on, and calling it a failure would tell them the rig
    // is still running while it is stopping exactly as asked.
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    if (timedOut) {
      toast({ level: "warning", title: `${label} sent - the rig is still winding down` });
      return { ok: false, timedOut: true, status: null };
    }
    toast({ level: "error", title: `${label} failed - link may be down` });
    return { ok: false, timedOut: false, status: null };
  }
}
